import json
import os
import pytest


@pytest.mark.critical('验证测试环境上下文', author='Axiom', grade='Critical')
def test_context():
    context = json.loads(os.environ['AXIOM_CONTEXT_JSON'])
    assert context['code']
    assert 'variables' in context


class TestDevice:
    @pytest.mark.high('验证设备状态数据', author='Axiom')
    def test_device_state(self):
        device = {'power': 'ON', 'brightness': 80}
        assert device['power'] == 'ON'
        assert 0 <= device['brightness'] <= 100
