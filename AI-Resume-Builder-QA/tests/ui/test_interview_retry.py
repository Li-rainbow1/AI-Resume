# author: jf
import json

import pytest
from playwright.sync_api import expect

from pages.login_page import LoginPage


@pytest.mark.ui
def test_interview_retry_replaces_partial_reply(page, qa_settings, ui_environment):
    # 在浏览器请求边界模拟断流，避免创建实际面试数据或调用真实模型。
    page.route('**/api/ai/interview/sessions?*', lambda route: route.fulfill(json=[]))
    requests = []

    def respond(route):
        payload = route.request.post_data_json
        requests.append(payload)
        events = [{'event': 'accepted', 'data': '已收到'}]
        if len(requests) == 1:
            events.append({'event': 'chunk', 'data': 'QA 半截回答'})
            # 正常关闭 HTTP，但没有 done，前端必须视为失败。
        elif len(requests) == 2:
            events.append({'event': 'error', 'data': 'QA 第二次失败'})
        else:
            done = {'assistantReply': 'QA 完整回答', 'phase': 'opening',
                    'nextAction': 'continue', 'turnScore': None, 'finalEvaluation': None,
                    'sessionId': payload['sessionId'], 'memorySummary': ''}
            events.append({'event': 'done', 'data': json.dumps(done, ensure_ascii=False)})
        route.fulfill(content_type='application/x-ndjson', body='\n'.join(json.dumps(e, ensure_ascii=False) for e in events)+'\n')

    page.route('**/api/ai/interview/turn/stream', respond)
    login = LoginPage(page, qa_settings.ui_base_url)
    login.open()
    login.login_as_admin(qa_settings.admin_username, qa_settings.admin_password)
    page.get_by_role('link', name='AI 面试', exact=True).click()
    start = page.get_by_role('button', name='开始面试', exact=True)
    start.click()
    expect(page.locator('.incomplete-tip')).to_have_count(1)
    start.click()
    expect(page.get_by_text('QA 第二次失败', exact=True)).to_be_visible()
    expect(page.locator('.incomplete-tip')).to_have_count(1)
    start.click()
    expect(page.get_by_text('QA 完整回答', exact=True)).to_be_visible()
    expect(page.locator('.incomplete-tip')).to_have_count(0)
    expect(page.get_by_text('QA 半截回答', exact=True)).to_have_count(0)
    expect(page.locator('.chat-item.assistant')).to_have_count(1)
    assert len(requests) == 3
    assert len({request['requestId'] for request in requests}) == 1
    assert len({request['sessionId'] for request in requests}) == 1
