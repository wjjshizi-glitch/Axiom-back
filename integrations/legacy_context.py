"""Copy this file into the legacy repository as utils/platform_context.py."""
import json
import os


def get_full_env_context(username=None):
    from utils.attribute_dict import AttributeDict
    raw = os.environ.get('AXIOM_CONTEXT_JSON')
    if not raw:
        raise RuntimeError('Run from Axiom worker or provide AXIOM_CONTEXT_JSON')
    env = json.loads(raw)
    context = {
        'env_code': env['code'], 'owner': username or os.getenv('TARGET_USERNAME'),
        'endpoints': env.get('legacy_endpoints', {}), 'cloud': None, 'local': None,
    }
    connections = env.get('secrets', {}).get('connections', {})
    context['cloud'] = {
        'db': (connections.get('mysql') or connections.get('postgresql') or [{}])[0],
        'redis': (connections.get('redis') or [{}])[0],
        'mqtt': (connections.get('mqtt') or [{}])[0],
        'ssh': {}, 'endpoints': {key:value['base_url'] for key,value in env.get('systems_by_key', {}).items()},
    }
    return AttributeDict(context)
