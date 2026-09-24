"""Durable queue worker. Each run owns its HTTP session and configuration."""
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse
import requests
from django.conf import settings
from django.utils import timezone
from .models import TestRun, CaseResult
from .security import decrypt

SENSITIVE = re.compile(r'password|passwd|pwd|secret|token|authorization|cookie|api.key', re.I)
PLACEHOLDER = re.compile(r'\{\{\s*([\w.-]+)\s*\}\}')


def render(value, variables):
    if isinstance(value, dict):
        return {k: render(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, variables) for v in value]
    if not isinstance(value, str):
        return value
    def substitute(match):
        key = match.group(1)
        if key not in variables:
            raise ValueError(f'变量未定义: {key}')
        return str(variables[key])
    match = PLACEHOLDER.fullmatch(value)
    if match and match.group(1) in variables:
        return variables[match.group(1)]
    return PLACEHOLDER.sub(substitute, value)


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {k: '***' if SENSITIVE.search(str(k)) else redact(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if isinstance(secret, (str, int)) and len(str(secret)) >= 4:
                value = value.replace(str(secret), '***')
    return value


def leaf_values(value):
    if isinstance(value, dict):
        return [x for v in value.values() for x in leaf_values(v)]
    if isinstance(value, list):
        return [x for v in value for x in leaf_values(v)]
    return [value]


def json_path(data, path):
    path = str(path or '').removeprefix('$').lstrip('.')
    for key in re.findall(r'[^.\[\]]+', path):
        data = data[int(key)] if isinstance(data, list) else data[key]
    return data


def validate_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ['http', 'https'] or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('只允许不含凭据的 HTTP(S) URL')
    if parsed.hostname.lower() not in settings.RUNNER_ALLOWED_HOSTS:
        raise ValueError(f'目标主机 {parsed.hostname} 未配置在 RUNNER_ALLOWED_HOSTS')
    if parsed.hostname == '169.254.169.254':
        raise ValueError('禁止访问云元数据地址')
    return url


def evaluate(rule, response, body, elapsed):
    operator = rule.get('operator', 'eq')
    try:
        source = rule['source']
        actual = {'status': response.status_code, 'text': response.text[:65536], 'duration': elapsed}.get(source)
        if source == 'json':
            actual = json_path(body, rule.get('path', ''))
        elif source == 'header':
            actual = response.headers[rule['path']]
        expected = rule.get('expected')
        if operator == 'eq':
            passed = actual == expected
        elif operator == 'ne':
            passed = actual != expected
        elif operator == 'contains':
            passed = expected in actual
        elif operator == 'exists':
            passed = actual is not None
        elif operator == 'lt':
            passed = actual < expected
        elif operator == 'gt':
            passed = actual > expected
        else:
            raise ValueError('不支持的断言')
        return {**rule, 'actual': actual, 'passed': passed}
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        return {**rule, 'actual': None, 'passed': False, 'error': str(exc)}


def run_http(case, env, variables, session):
    endpoint = case.get('endpoint_data')
    if not endpoint:
        raise ValueError('关联接口已删除')
    config = render({**endpoint, **case.get('request_overrides', {})}, variables)
    system_key = config['system_key']
    system = env.get('systems_by_key', {}).get(system_key)
    if not system:
        raise ValueError(f'当前环境未配置系统: {system_key}')
    base = system['base_url']
    url = validate_url(urljoin(str(render(base, variables)).rstrip('/') + '/', str(config['path'])))
    headers = {
        **(render(system.get('headers', {}), variables)
           if config.get('use_auth', True) else {}),
        **config.get('headers', {})}
    authentication = system.get('auth_config', {})
    if config.get('use_auth', True) and authentication:
        auth_token = env.get('auth_tokens', {}).get(system_key, {})
        if auth_token.get('expires_at') and datetime.fromisoformat(auth_token['expires_at']) <= timezone.now():
            raise ValueError('被测系统 Token 已过期，请重新登录后执行')
        if not auth_token.get('token'):
            raise ValueError(f'当前账号尚未登录系统 {system_key}，或 Token 已过期')
        header = authentication.get('header_name', 'Authorization')
        prefix = authentication.get('prefix', 'Bearer')
        headers[header] = (prefix + ' ' if prefix else '') + str(auth_token['token'])
    kwargs = {'params': config.get('query', {}), 'headers': headers,
              'timeout': (5, settings.RUNNER_TIMEOUT), 'verify': system['verify_ssl'],
              'allow_redirects': False, 'stream': True}
    body = config.get('body', {})
    kwargs['json' if config['body_type'] == 'json' else 'data'] = body
    started = time.monotonic()
    with session.request(config['method'], url, **kwargs) as response:
        chunks, size = [], 0
        for chunk in response.iter_content(8192):
            size += len(chunk)
            if size > 1024 * 1024 or time.monotonic() - started > settings.RUNNER_TIMEOUT:
                raise ValueError('响应超过 1 MB 或请求总时长限制')
            chunks.append(chunk)
        response._content = b''.join(chunks)
        elapsed = (time.monotonic() - started) * 1000
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
        rules = render(case.get('assertions') or [{'source': 'status', 'operator': 'eq', 'expected': 200}], variables)
        assertions = [evaluate(rule, response, parsed, elapsed) for rule in rules]
        for name, path in case.get('extract', {}).items():
            variables[name] = json_path(parsed, path)
        return {'status': 'passed' if all(a['passed'] for a in assertions) else 'failed',
                'duration_ms': elapsed, 'assertions': assertions,
                'request': {'method': config['method'], 'url': url, 'headers': headers,
                            'query': config.get('query', {}), 'body': body},
                'response': {'status': response.status_code, 'headers': dict(response.headers),
                             'body': parsed if parsed is not None else response.text[:65536]}}


def run_pytest(case, snapshot, run_id):
    parts = case['node_id'].split('::')
    file = (settings.PYTEST_ROOT / parts[0]).resolve()
    if not file.is_relative_to(settings.PYTEST_ROOT) or not file.is_file() or file.suffix != '.py':
        raise ValueError('Python 用例路径不在受信任目录内')
    if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) for name in parts[1:]):
        raise ValueError('非法 Pytest node ID')
    target = '::'.join([str(file), *parts[1:]])
    run_env = os.environ.copy()
    run_env.update({
        'TARGET_USERNAME': snapshot['username'], 'TEST_USER': snapshot['username'],
        'TARGET_ENV_CODE': snapshot['environment']['code'],
        'AXIOM_CONTEXT_JSON': json.dumps(snapshot['environment']),
        'PYTHONPATH': os.pathsep.join(filter(None, [
            str(settings.BASE_DIR), run_env.get('PYTHONPATH', '')])),
        'PYTHONIOENCODING': 'utf-8',
    })
    started = time.monotonic()
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            [settings.PYTEST_PYTHON or sys.executable, '-m', 'pytest', target, '-v', '--tb=short',
             '--disable-warnings', '-o', 'addopts='],
            cwd=settings.PYTEST_WORKDIR, env=run_env, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True)
        try:
            while process.poll() is None:
                if time.monotonic() - started > settings.RUNNER_RUN_TIMEOUT:
                    raise TimeoutError('Pytest 执行超时')
                if TestRun.objects.filter(pk=run_id, cancel_requested=True).exists():
                    raise InterruptedError('任务已取消')
                TestRun.objects.filter(pk=run_id).update(heartbeat_at=timezone.now())
                time.sleep(0.5)
        finally:
            if process.poll() is None:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    if os.name == 'posix':
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait()
        output.seek(0, 2)
        length = output.tell()
        output.seek(max(0, length - 65536))
        log = output.read().decode('utf-8', errors='replace')
    return {'status': 'passed' if process.returncode == 0 else 'failed',
            'duration_ms': (time.monotonic() - started) * 1000, 'log': log,
            'error': '' if process.returncode == 0 else f'pytest exit code: {process.returncode}',
            'assertions': [], 'request': {}, 'response': {}}


def execute_run(run):
    started = time.monotonic()
    secrets = []
    try:
        snapshot = decrypt(run.snapshot)
        env = snapshot['environment']
        variables = {**env['variables'], **env['secrets'].get('variables', {})}
        secrets = leaf_values(env['secrets']) + leaf_values(env.get('headers', {}))
        secrets += leaf_values(env.get('system_credentials', {}))
        with requests.Session() as session:
            session.trust_env = False
            for case in snapshot['cases']:
                run.refresh_from_db()
                if run.cancel_requested:
                    run.status = 'cancelled'
                    break
                if time.monotonic() - started > settings.RUNNER_RUN_TIMEOUT:
                    raise TimeoutError('任务总时长超限')
                try:
                    result = (run_pytest(case, snapshot, run.pk) if case['kind'] == 'pytest'
                              else run_http(case, env, variables, session))
                except InterruptedError:
                    run.status = 'cancelled'
                    break
                except Exception as exc:
                    result = {'status': 'error', 'error': str(exc)}
                secrets += [v for k, v in variables.items() if SENSITIVE.search(k)]
                result = redact(result, secrets)
                CaseResult.objects.create(run=run, name=case['name'], case_id_snapshot=case['id'], **result)
                if result['status'] == 'passed':
                    run.passed += 1
                else:
                    run.failed += 1
                run.log += f"[{result['status'].upper()}] {case['name']}\n"
                run.heartbeat_at = timezone.now()
                run.save(update_fields=['passed', 'failed', 'log', 'heartbeat_at'])
                if snapshot['stop_on_failure'] and result['status'] != 'passed':
                    break
            if run.status != 'cancelled':
                run.status = 'failed' if run.failed else 'passed'
    except Exception as exc:
        run.status = 'error'
        run.log += redact(str(exc), secrets)
    finally:
        if TestRun.objects.filter(pk=run.pk, cancel_requested=True).exists():
            run.status = 'cancelled'
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'log', 'finished_at'])
