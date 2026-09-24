"""Data-driven API client for Python tests executed by Axiom."""
import json
import os
import re
from urllib.parse import urljoin
import requests

VARIABLE = re.compile(r'\{\{\s*([\w.-]+)\s*\}\}')
PATH_VARIABLE = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)\}')


class AxiomResponse:
    def __init__(self, response):
        self.raw = response
        self.status_code = response.status_code
        self.headers = dict(response.headers)
        try:
            self.data = response.json()
        except ValueError:
            self.data = response.text

    def assert_success(self, message='接口请求失败'):
        if not 200 <= self.status_code < 300:
            raise AssertionError(f'{message}: HTTP {self.status_code}')
        return self


class AxiomApiClient:
    def __init__(self, context=None, timeout=30):
        context = context or json.loads(os.environ['AXIOM_CONTEXT_JSON'])
        self.environment = context
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False
        self.endpoints = {item['function_name']: item for item in context.get('api_catalog', [])}
        self.variables = {**context.get('variables', {}), **context.get('secrets', {}).get('variables', {})}

    def __getattr__(self, name):
        if name in self.endpoints:
            return lambda **kwargs: self.call(name, **kwargs)
        raise AttributeError(f'接口方法 {name!r} 未定义，可用方法: {", ".join(sorted(self.endpoints))}')

    def _render(self, value, variables):
        if isinstance(value, dict):
            return {key: self._render(item, variables) for key, item in value.items()}
        if isinstance(value, list):
            return [self._render(item, variables) for item in value]
        if not isinstance(value, str):
            return value
        match = VARIABLE.fullmatch(value)
        if match and match.group(1) in variables:
            return variables[match.group(1)]
        def replace(item):
            if item.group(1) not in variables:
                raise KeyError(f'变量未定义: {item.group(1)}')
            return str(variables[item.group(1)])
        return VARIABLE.sub(replace, value)

    def call(self, method_name, *, path=None, query=None, headers=None, body=None,
             variables=None, use_auth=None, timeout=None):
        if method_name not in self.endpoints:
            raise KeyError(f'接口方法不存在: {method_name}')
        endpoint = self.endpoints[method_name]
        request_path = endpoint['path']
        for name in PATH_VARIABLE.findall(request_path):
            if name not in (path or {}):
                raise KeyError(f'路径参数未提供: {name}')
            request_path = request_path.replace('{' + name + '}', str(path[name]))
        key = endpoint['system_key']
        system = self.environment.get('systems_by_key', {}).get(key)
        if not system:
            raise ValueError(f'当前环境未配置系统: {key}')
        base = system['base_url']
        merged_variables = {
            **self.variables,
            **self.environment.get('system_credentials', {}).get(key, {}),
            **(variables or {})}
        request_headers = {**self.environment.get('headers', {}), **endpoint.get('headers', {}), **(headers or {})}
        request_query = {**endpoint.get('query', {}), **(query or {})}
        request_body = endpoint.get('body', {}) if body is None else body
        should_auth = endpoint.get('use_auth', True) if use_auth is None else use_auth
        auth = system.get('auth_config', {})
        if should_auth and auth:
            token = self.environment.get('auth_tokens', {}).get(key, {}).get('token')
            if not token:
                raise RuntimeError('当前账号尚未登录此环境的被测系统，或 Token 已过期')
            prefix = auth.get('prefix', 'Bearer')
            request_headers[auth.get('header_name', 'Authorization')] = (
                (prefix + ' ') if prefix else '') + str(token)
        url = urljoin(str(base).rstrip('/') + '/', request_path.lstrip('/'))
        kwargs = {
            'params': self._render(request_query, merged_variables),
            'headers': self._render(request_headers, merged_variables),
            'timeout': timeout or self.timeout,
            'verify': system.get('verify_ssl', True),
            'allow_redirects': False,
        }
        if endpoint.get('body_type') == 'json':
            kwargs['json'] = self._render(request_body, merged_variables)
        else:
            kwargs['data'] = self._render(request_body, merged_variables)
        response = self.session.request(endpoint['method'], url, **kwargs)
        return AxiomResponse(response)
