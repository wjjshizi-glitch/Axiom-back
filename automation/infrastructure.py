import re
from urllib.parse import urlparse
from rest_framework import serializers

KEY = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]*$')


def validate_systems(systems):
    if not isinstance(systems, list):
        raise serializers.ValidationError({'systems': '系统配置必须是数组'})
    keys = set()
    cleaned = []
    for item in systems:
        if not isinstance(item, dict):
            raise serializers.ValidationError({'systems': '每个系统配置必须是对象'})
        key = item.get('system_key', '')
        url = item.get('base_url', '')
        if not KEY.fullmatch(key) or key in keys:
            raise serializers.ValidationError({'systems': 'system_key 格式无效或重复'})
        parsed = urlparse(url)
        if parsed.scheme not in ['http', 'https'] or not parsed.hostname:
            raise serializers.ValidationError({'systems': f'{key} 必须配置完整 HTTP(S) 地址'})
        auth = item.get('auth_config') or {}
        if not isinstance(auth, dict):
            raise serializers.ValidationError({'systems': f'{key} 的登录规则必须是对象'})
        if auth:
            if not isinstance(auth.get('path'), str) or not auth['path'].startswith('/') or auth['path'].startswith('//'):
                raise serializers.ValidationError({'systems': f'{key} 的登录 path 必须是相对路径'})
            if not isinstance(auth.get('token_path'), str) or not auth['token_path']:
                raise serializers.ValidationError({'systems': f'{key} 缺少 token_path'})
            if not isinstance(auth.get('body', {}), dict) or not isinstance(auth.get('headers', {}), dict):
                raise serializers.ValidationError({'systems': f'{key} 的 body/headers 必须是对象'})
            if auth.get('body_type', 'json') not in ['json', 'form']:
                raise serializers.ValidationError({'systems': f'{key} 登录格式仅支持 json/form'})
            ttl = auth.get('expires_in', 3600)
            if type(ttl) is not int or not 60 <= ttl <= 86400:
                raise serializers.ValidationError({'systems': f'{key} 的 expires_in 范围为 60～86400'})
        secret = item.get('credentials')
        if secret is not None and not isinstance(secret, dict):
            raise serializers.ValidationError({'systems': f'{key} 的认证凭据必须是对象'})
        login_extract = item.get('login_extract') or {}
        if not isinstance(login_extract, dict) or any(
                not isinstance(name, str) or not name or
                not isinstance(path, str) or not path
                for name, path in login_extract.items()):
            raise serializers.ValidationError({
                'systems': f'{key} 的登录响应变量必须是变量名到 JSON 路径的对象'})
        login_expires_in = item.get('login_expires_in', 3600)
        if type(login_expires_in) is not int or not 60 <= login_expires_in <= 86400:
            raise serializers.ValidationError({
                'systems': f'{key} 的登录变量有效期范围为 60～86400 秒'})
        headers = item.get('headers') or {}
        if not isinstance(headers, dict):
            raise serializers.ValidationError({
                'systems': f'{key} 的公共请求头必须是 JSON 对象'})
        keys.add(key)
        cleaned.append({
            'system_key': key, 'name': str(item.get('name') or key)[:100],
            'base_url': url, 'verify_ssl': bool(item.get('verify_ssl', True)),
            'auth_config': auth, 'credentials': secret,
            'login_endpoint': item.get('login_endpoint'),
            'login_extract': login_extract,
            'login_expires_in': login_expires_in,
            'headers': headers,
        })
    return cleaned


def validate_connections(data):
    if not isinstance(data, dict):
        raise serializers.ValidationError({'connections': '基础设施配置必须是对象'})
    allowed = {'mysql', 'postgresql', 'redis', 'mqtt'}
    if set(data) - allowed:
        raise serializers.ValidationError({'connections': '仅支持 MySQL、PostgreSQL、Redis、MQTT'})
    defaults = {'mysql': 3306, 'postgresql': 5432, 'redis': 6379, 'mqtt': 1883}
    cleaned = {}
    for kind, rows in data.items():
        if not isinstance(rows, list):
            raise serializers.ValidationError({'connections': f'{kind} 必须是数组'})
        names = set()
        cleaned[kind] = []
        for row in rows:
            if not isinstance(row, dict) or not row.get('name') or not row.get('host'):
                raise serializers.ValidationError({'connections': f'{kind} 需要 name 和 host'})
            if row['name'] in names:
                raise serializers.ValidationError({'connections': f'{kind} 连接名称不能重复'})
            port = row.get('port', defaults[kind])
            if type(port) is not int or not 1 <= port <= 65535:
                raise serializers.ValidationError({'connections': f'{kind} 端口无效'})
            if kind in ['mysql', 'postgresql'] and not row.get('database'):
                raise serializers.ValidationError({'connections': f'{kind} 需要数据库名'})
            if kind == 'redis' and row.get('database') not in [None, ''] and not str(row['database']).isdigit():
                raise serializers.ValidationError({'connections': 'Redis DB 必须是非负整数'})
            if row.get('extra') not in [None, ''] and not isinstance(row['extra'], dict):
                raise serializers.ValidationError({'connections': f'{kind} 的附加参数必须是对象'})
            names.add(row['name'])
            cleaned[kind].append({
                key: value for key, value in row.items()
                if key in ['name', 'host', 'port', 'username', 'password',
                           'database', 'extra', 'client_id', 'topic_prefix',
                           'ssl', 'qos']})
    return cleaned
