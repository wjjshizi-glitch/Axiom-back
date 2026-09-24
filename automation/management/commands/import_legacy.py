import json
from pathlib import Path
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from automation.models import Project, Environment, EnvironmentSystem, Endpoint
from automation.security import encrypt


class Command(BaseCommand):
    help = 'Import legacy Django dumpdata JSON for environments and endpoints into one owner/project.'

    def add_arguments(self, parser):
        parser.add_argument('file')
        parser.add_argument('--owner', required=True)
        parser.add_argument('--project', required=True, type=int)
        parser.add_argument('--legacy-owner-id', type=int, help='Required when fixture has multiple owners')

    @transaction.atomic
    def handle(self, *args, **options):
        owner = get_user_model().objects.filter(username=options['owner']).first()
        project = Project.objects.filter(pk=options['project']).first()
        if not owner or not project:
            raise CommandError('Owner or project not found')
        records = json.loads(Path(options['file']).read_text(encoding='utf-8'))
        owners = {r['fields'].get('owner') for r in records if r['model'].endswith('.nansenvconfig')}
        if len(owners) > 1 and options['legacy_owner_id'] is None:
            raise CommandError('Multiple owners found. Specify --legacy-owner-id to avoid merging private environments.')
        count = 0
        for record in records:
            f = record['fields']
            if record['model'].endswith('.nansenvconfig'):
                if options['legacy_owner_id'] is not None and f.get('owner') != options['legacy_owner_id']:
                    continue
                private = {}
                for section, mapping in {
                    'db': {'type':'db_type','host':'db_host','port':'db_port','user':'db_user','pwd':'db_pwd','name':'db_name'},
                    'redis': {'host':'redis_host','port':'redis_port','password':'redis_password','db':'redis_db'},
                    'mqtt': {'host':'mqtt_host','port':'mqtt_port','user':'mqtt_user','pwd':'mqtt_pwd'},
                    'ssh': {'use':'use_ssh','host':'ssh_host','port':'ssh_port','user':'ssh_user','pwd':'ssh_pwd'},
                }.items():
                    private[section] = {key:f.get(source) for key,source in mapping.items()}
                env, _ = Environment.objects.update_or_create(
                    owner=owner, project=project, scope='private',
                    code=f['env_code'],
                    defaults={'name':f['env_name'],'credentials':encrypt({'connections': {
                        'mysql':[{'name':f['location'],'host':private['db']['host'],
                                  'port':private['db']['port'],'username':private['db']['user'],
                                  'password':private['db']['pwd'],'database':private['db']['name']}],
                        'redis':[{'name':f['location'],'host':private['redis']['host'],
                                  'port':private['redis']['port'],'password':private['redis']['password'],
                                  'database':private['redis']['db']}],
                        'mqtt':[{'name':f['location'],'host':private['mqtt']['host'],
                                 'port':private['mqtt']['port'],'username':private['mqtt']['user'],
                                 'password':private['mqtt']['pwd']}],
                    }})})
                for key, url in (f.get('endpoints') or {}).items():
                    EnvironmentSystem.objects.update_or_create(
                        environment=env, system_key=key,
                        defaults={'name':key,'base_url':url})
                count += 1
            elif record['model'].endswith('.serviceendpoint'):
                body = f.get('req_params') or '{}'
                try:
                    body = json.loads(body)
                except (ValueError, TypeError):
                    pass
                Endpoint.objects.update_or_create(project=project, system_key=f['service_key'],
                    function_name=f['service_name'].lower().replace('-', '_'), defaults={'name':f['address_name'],'path':f['url'],
                    'method':f.get('method','POST'),'body_type':{'JSON':'json','FORM':'form'}.get(f.get('req_format'),'text'),
                    'body':body,'description':f.get('note') or ''})
                count += 1
        self.stdout.write(self.style.SUCCESS(f'Imported {count} records. Credentials encrypted.'))
