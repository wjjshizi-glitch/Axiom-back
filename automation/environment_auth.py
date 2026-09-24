import hashlib
import json
from datetime import timedelta
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from .models import EnvironmentSession, EnvironmentSystem
from .security import decrypt, encrypt


def systems_for(env):
    source = env.public_environment if (
        env.scope == 'private' and env.public_environment_id) else env
    return source.systems.all()


def system_for(env, system_key):
    source = env.public_environment if (
        env.scope == 'private' and env.public_environment_id) else env
    return source.systems.filter(system_key=system_key).first()


def config_hash(system):
    return hashlib.sha256(json.dumps({
        'auth': system.auth_config, 'base_url': system.base_url,
        'verify_ssl': system.verify_ssl,
        'login_endpoint': system.login_endpoint_id,
        'login_endpoint_updated_at': (
            system.login_endpoint.updated_at.isoformat()
            if system.login_endpoint_id else None),
        'login_extract': system.login_extract,
    }, sort_keys=True).encode()).hexdigest()


def session_for(user, env, system_key):
    system = system_for(env, system_key)
    if not system:
        return None
    return EnvironmentSession.objects.filter(
        owner=user, environment=env, system_key=system_key,
        expires_at__gt=timezone.now(), config_hash=config_hash(system)).first()


def auth_status(user, env):
    result = {}
    for system in systems_for(env):
        session = session_for(user, env, system.system_key)
        result[system.system_key] = {
            'configured': bool(system.login_endpoint_id or system.auth_config),
            'authenticated': bool(session),
            'expires_at': session.expires_at if session else None}
    return result


def attach_sessions(data, user, env):
    data['auth_tokens'] = {}
    data['login_variables'] = {}
    for system in systems_for(env):
        session = session_for(user, env, system.system_key)
        if session:
            payload = decrypt(session.token)
            if payload.get('token'):
                data['auth_tokens'][system.system_key] = {
                    'token': payload['token'],
                    'expires_at': session.expires_at.isoformat()}
            data['login_variables'].update(payload.get('variables', {}))
    return data


def store_token(user, env, system, token):
    if not isinstance(token, str) or not token or len(token) > 16384:
        raise ValidationError('登录响应未提取到有效 Token，请检查提取路径')
    return EnvironmentSession.objects.update_or_create(
        owner=user, environment=env, system_key=system.system_key, defaults={
        'token': encrypt({'token': token}), 'config_hash': config_hash(system),
        'expires_at': timezone.now() + timedelta(seconds=system.auth_config.get('expires_in', 3600)),
    })[0]


def store_login_variables(user, env, system, variables):
    if not isinstance(variables, dict) or not variables:
        raise ValidationError('登录接口没有提取到响应变量，请检查提取规则')
    return EnvironmentSession.objects.update_or_create(
        owner=user, environment=env, system_key=system.system_key, defaults={
            'token': encrypt({'variables': variables}),
            'config_hash': config_hash(system),
            'expires_at': timezone.now() + timedelta(
                seconds=system.login_expires_in),
        })[0]
