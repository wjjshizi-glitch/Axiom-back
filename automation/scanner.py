import ast
from django.conf import settings
from .models import TestCase


def scan_cases(project):
    root = settings.PYTEST_ROOT.resolve()
    if not root.is_dir():
        raise ValueError('PYTEST_ROOT 不存在')
    added = updated = 0
    errors = []
    for path in sorted(root.rglob('test_*.py')):
        if not path.resolve().is_relative_to(root) or any(p.startswith('.') for p in path.relative_to(root).parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
        except (SyntaxError, UnicodeError) as exc:
            errors.append(f'{path.name}: {exc}')
            continue
        nodes = [(n, []) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for cls in [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name.startswith('Test')]:
            nodes.extend((n, [cls.name]) for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
        for node, parents in nodes:
            if not node.name.startswith('test_'):
                continue
            meta = {'name': ast.get_docstring(node) or node.name, 'author': '', 'priority': 'P1'}
            tags = []
            for deco in node.decorator_list:
                target = deco.func if isinstance(deco, ast.Call) else deco
                if not isinstance(target, ast.Attribute) or not isinstance(target.value, ast.Attribute):
                    continue
                if target.value.attr != 'mark':
                    continue
                tags.append(target.attr)
                meta['priority'] = {'critical': 'P0', 'high': 'P1', 'medium': 'P2', 'low': 'P3'}.get(target.attr, meta['priority'])
                if isinstance(deco, ast.Call):
                    if deco.args and isinstance(deco.args[0], ast.Constant) and isinstance(deco.args[0].value, str) and target.attr not in ['parametrize', 'skipif']:
                        meta['name'] = deco.args[0].value
                    for kw in deco.keywords:
                        if kw.arg == 'author' and isinstance(kw.value, ast.Constant):
                            meta['author'] = str(kw.value.value)
            node_id = '::'.join([path.relative_to(root).as_posix(), *parents, node.name])
            _, created = TestCase.objects.update_or_create(
                project=project, kind='pytest', node_id=node_id,
                defaults={**meta, 'tags': tags, 'description': ast.get_docstring(node) or ''})
            added += int(created)
            updated += int(not created)
    return {'added': added, 'updated': updated, 'errors': errors}
