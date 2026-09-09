# author: jf
"""编排现有命令，所有业务创建和清理由既有 Fixture 负责。"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {
    'image_worker_comparison': 'IMAGE_WORKER',
    'interview_sse': 'INTERVIEW',
}
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from clients.auth import AuthClient
from clients.rag import RagClient
from fixtures.config import QaSettings
import httpx


def command(args):
    return subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')


async def health(real_allowed):
    settings = QaSettings.from_environment()
    compose = ['docker', 'compose', '--env-file', '.env.test', '-f', 'compose.qa.yml']
    checked = command(compose + ['config', '--quiet'])
    if checked.returncode:
        raise RuntimeError('隔离 Compose 配置无效，请检查 Docker 和 .env.test')
    services_to_check = ('backend', 'mysql', 'redis', 'pgvector', 'minio', 'image-worker')
    if not real_allowed:
        services_to_check += ('frontend', 'mock-ai')
    for service in services_to_check:
        result = command(compose + ['ps', '-q', service])
        cid = result.stdout.strip()
        if result.returncode or not cid:
            raise RuntimeError(f'隔离 Compose 服务未就绪：{service}')
        state = command(['docker', 'inspect', '--format', '{{json .State}}', cid])
        value = json.loads(state.stdout)
        if not value.get('Running') or value.get('Health', {}).get('Status', 'healthy') != 'healthy':
            raise RuntimeError(f'隔离 Compose 服务不健康：{service}')
        # 核验运行中的容器，避免只检查配置文件却连接其他环境。
        if service in {'backend', 'frontend'}:
            ports = json.loads(command(['docker', 'inspect', '--format', '{{json .NetworkSettings.Ports}}', cid]).stdout)
            target = urlparse(settings.base_url if service == 'backend' else settings.ui_base_url)
            internal = '8999/tcp' if service == 'backend' else '80/tcp'
            if target.hostname not in {'localhost', '127.0.0.1'} or target.scheme != 'http' or target.username:
                raise RuntimeError('回归只允许本机隔离 QA HTTP 地址')
            if not any(p['HostIp'] == '127.0.0.1' and int(p['HostPort']) == target.port for p in ports.get(internal, [])):
                raise RuntimeError(f'目标地址与隔离容器端口不一致：{service}')
        if service in {'backend', 'image-worker'} and not real_allowed:
            values = json.loads(command(['docker', 'inspect', '--format', '{{json .Config.Env}}', cid]).stdout)
            env = dict(item.split('=', 1) for item in values)
            kinds = ('EMBEDDING', 'VISION') if service == 'image-worker' else ('CHAT', 'EMBEDDING', 'VISION')
            if any(urlparse(env.get(f'OPENAI_{kind}_BASE_URL', '')).hostname != 'mock-ai' for kind in kinds):
                raise RuntimeError(f'{service} 未完全指向 Mock AI，拒绝默认回归')
    async with httpx.AsyncClient(base_url=settings.base_url, timeout=15) as client:
        (await client.get('/health')).raise_for_status()
        if not real_allowed:
            (await client.get(settings.ui_base_url)).raise_for_status()
        session = await AuthClient(client).login_admin(settings.admin_username, settings.admin_password)
        client.headers['Authorization'] = f'Bearer {session.access_token}'
        services = await RagClient(client).list_system_services()
        if real_allowed:
            from quality.runtime_guard import real_model_guard_reason
            reason = await real_model_guard_reason(RagClient(client))
            if reason:
                raise RuntimeError(reason)
        if not real_allowed:
            configs = {s['serviceKey']: s['config'] for s in services}
            if any(urlparse(configs.get(k, {}).get('baseUrl', '')).hostname != 'mock-ai' for k in ('chat', 'embedding', 'vision')):
                raise RuntimeError('管理员生效配置未完全指向 Mock AI，拒绝默认回归')
    if real_allowed:
        return
    # 默认回归真实启动浏览器验证依赖。
    from playwright.sync_api import sync_playwright
    def browser_check():
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            browser.close()
    await asyncio.to_thread(browser_check)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--performance', default='')
    parser.add_argument('--quality', action='store_true')
    args = parser.parse_args()
    os.chdir(ROOT)
    args.report.mkdir(parents=True, exist_ok=True)
    load_dotenv(ROOT / '.env.test', override=False)
    # 默认拒绝继承用户终端或本地配置中开启的高风险开关。
    selected = list(dict.fromkeys(item.strip() for item in args.performance.split(',') if item.strip())) if args.performance else []
    for key in list(os.environ):
        if key.startswith('PERF_RUN_') or (not selected and key.startswith('PERF_ALLOW_')):
            os.environ[key] = '0'
    if not args.quality:
        for key in ('QA_RUN_RAG_QUALITY', 'QA_ALLOW_QUALITY_WRITES', 'QA_QUALITY_REAL_MODELS_CONFIRMED', 'QA_RUN_DEEPEVAL'):
            os.environ[key] = '0'
    os.environ['QA_RUN_UI'] = '0' if args.quality else '1'
    if not args.quality:
        os.environ.update(QA_RUN_RAG_INTEGRATION='1', QA_ALLOW_RAG_WRITES='1', QA_RUN_LIVE_CONTRACT='1')
    os.environ['PYTEST_ADDOPTS'] = ''
    stages = []
    started = time.monotonic()

    def run_stage(name, argv, junit=None):
        begin = time.monotonic()
        result = command(argv)
        code = result.returncode
        counts = {}
        if junit and code == 0:
            try:
                suites = ET.parse(junit).getroot().findall('.//testsuite')
                counts = {key: sum(int(s.get(key, 0)) for s in suites) for key in ('tests', 'failures', 'errors', 'skipped')}
                if not counts['tests'] or counts['skipped'] or counts['failures'] or counts['errors']:
                    code = 1
            except Exception:
                code = 1
        # 原始输出仅在内存中脱敏后落盘，避免原生错误夹带凭据。
        output = result.stdout + result.stderr
        for key, value in os.environ.items():
            if any(word in key for word in ('PASSWORD', 'TOKEN', 'SECRET', 'API_KEY', 'USERNAME')) and len(value) >= 4:
                output = output.replace(value, '[已隐藏]')
        (args.report / f'{name}.log').write_text(output, encoding='utf-8')
        stages.append(dict(name=name, exit_code=code, native_exit_code=result.returncode, seconds=round(time.monotonic()-begin, 2), **counts))
        print(f'{name}: exit={code} {counts}', flush=True)

    def pytest_stage(name, paths):
        junit = args.report / f'{name}.xml'
        run_stage(name, [sys.executable, '-m', 'pytest', *paths, '-q', '-o', 'addopts=', '--tb=short',
            f'--junitxml={junit}', f'--basetemp={args.report / (name + "-temp")}',
            f'--alluredir={args.report / "allure-results"}', f'--html={args.report / (name + ".html")}', '--self-contained-html',
            '--browser', 'chromium', '--screenshot', 'only-on-failure', '--output', str(args.report / 'playwright')], junit)
    try:
        if selected and args.quality:
            raise RuntimeError('性能与质量评测模型要求不同，请分次执行')
        if any(name not in SCENARIOS for name in selected):
            raise RuntimeError('未知性能场景')
        asyncio.run(health(args.quality))
        if args.quality:
            from quality.settings import QualitySettings
            reason = QualitySettings.load().skip_reason(QaSettings.from_environment(), require_judge=True)
            if reason:
                raise RuntimeError(reason)
        stages.append(dict(name='health', exit_code=0))
        if args.quality:
            pytest_stage('quality', ['tests/quality/test_real_rag_quality.py'])
        else:
            pytest_stage('api-mock', ['tests/api', 'tests/mock', 'tests/clients', 'tests/interview'])
            pytest_stage('ui', ['tests/ui/test_markdown_image_preview.py', 'tests/ui/test_interview_retry.py'])
        if selected:
            for name in selected:
                os.environ['PERF_RUN_' + SCENARIOS[name]] = '1'
                report_root = args.report / name
                if name == 'image_worker_comparison':
                    command_args = [
                        sys.executable,
                        'performance/run_image_worker_comparison.py',
                        '--report-root',
                        str(report_root),
                        '--run-id',
                        os.environ['QA_RUN_ID'],
                    ]
                else:
                    command_args = [
                        sys.executable,
                        'performance/run_interview_sse_baseline.py',
                        '--report-root',
                        str(report_root),
                        '--run-id',
                        os.environ['QA_RUN_ID'],
                    ]
                run_stage(name, command_args)
    except Exception as exc:
        # 第三方异常不打印正文，避免连接信息泄露。
        reason = str(exc) if isinstance(exc, RuntimeError) else f'{type(exc).__name__}：请检查 Docker、Playwright Chromium 和隔离环境依赖'
        stages.append(dict(name='preflight-or-orchestration', exit_code=1, reason=reason))
        print(reason, flush=True)
    finally:
        try:
            run_stage('cleanup', [sys.executable, 'scripts/verify_run_cleanup.py'])
        except Exception:
            stages.append(dict(name='cleanup', exit_code=1, reason='清理校验未能完成'))
        code = next((s['exit_code'] for s in stages if s['exit_code']), 0)
        summary = dict(author='jf', run_id=os.environ['QA_RUN_ID'], exit_code=code,
            performance=selected, quality=args.quality,
            seconds=round(time.monotonic()-started, 2), stages=stages, report_path=str(args.report))
        (args.report / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
