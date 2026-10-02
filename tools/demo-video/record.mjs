// mini-mes 시연 영상 시나리오 (rec.sh가 실행·녹화). MOCK=1이면 /query를 가짜 응답으로 대신 (LLM 호출 없음)
// 녹화 시작·종료는 ready/stop 파일로 rec.sh에 알린다 (전체 화면 전환 안내가 사라진 뒤 시작)
import { chromium } from 'playwright';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const B = process.env.BASE || 'https://mes.pmhllll12.cloud';
const MOCK = process.env.MOCK === '1';
const W = 1280, H = 720; // 화면 배율 1.5로 렌더링해 1920x1080으로 녹화
const Q1 = '최근 24시간 동안 이상이 가장 많이 탐지된 설비는?';
const Q2 = 'EQ-003의 어제 불량을 유형별로 알려줘';

// Xvfb(1920x1080) 위에 실제 창을 1.5배율·키오스크로 띄우고, 녹화는 바깥의 ffmpeg x11grab이 맡는다 (rec.sh)
// 번역 팝업은 실행 인자로 꺼지지 않아 프로필 설정으로 끈다
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'rec-'));
fs.mkdirSync(path.join(profile, 'Default'));
fs.writeFileSync(path.join(profile, 'Default', 'Preferences'), JSON.stringify({ translate: { enabled: false }, intl: { accept_languages: 'ko-KR,ko' } }));
const LAUNCH = { headless: false, args: ['--force-device-scale-factor=1.5', '--window-size=1280,720', '--window-position=0,0', '--hide-scrollbars',
  '--disable-features=Translate,TranslateUI', '--lang=ko-KR', '--disable-infobars'] };
const ctx = await chromium.launchPersistentContext(profile, { ...LAUNCH, viewport: null, locale: 'ko-KR', timezoneId: 'Asia/Seoul' });

// 모든 페이지에 하단 자막 상자
await ctx.addInitScript(() => {
  window.__cap = (text, top = false) => {
    let el = document.getElementById('__cap');
    if (!el) {
      el = document.createElement('div');
      el.id = '__cap';
      el.style.cssText = 'position:fixed;left:0;right:0;margin:0 auto;width:fit-content;z-index:2147483647;' +
        'max-width:1100px;padding:12px 24px;border-radius:12px;background:rgba(17,24,39,.88);color:#fff;' +
        'font:600 21px/1.45 "Malgun Gothic","Noto Sans KR",sans-serif;text-align:center;box-shadow:0 6px 24px rgba(0,0,0,.35);' +
        'transition:opacity .3s;pointer-events:none;white-space:pre-line';
      document.documentElement.appendChild(el);
    }
    el.style.top = top ? '20px' : 'auto';
    el.style.bottom = top ? 'auto' : '28px';
    el.style.opacity = text ? '1' : '0';
    if (text) el.textContent = text;
  };
});

if (MOCK) {
  await ctx.route('**/query', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    await new Promise((r) => setTimeout(r, 4000));
    const anomalies = await (await fetch(`${B}/anomalies`)).json();
    route.fulfill({ json: {
      answer: '(MOCK) 최근 24시간 동안 이상이 가장 많이 탐지된 설비는 **EQ-001**입니다.\n- EQ-001: 8건\n- EQ-002: 5건',
      provider: 'gemini', model: 'mock', stop: 'answer',
      tool_calls: [{ name: 'get_anomalies', input: { hours: 24 }, ok: true,
        result: anomalies.map(({ anomalies: _, ...r }) => r) }],
    } });
  });
}

const page = ctx.pages()[0] || await ctx.newPage();
// 탭·주소창 없이 화면 전체를 페이지로 (CDP 전체 화면)
{
  const cdp = await ctx.newCDPSession(page);
  const { windowId } = await cdp.send('Browser.getWindowForTarget');
  await cdp.send('Browser.setWindowBounds', { windowId, bounds: { windowState: 'fullscreen' } });
  await page.waitForTimeout(500);
}
const cap = (t, top = false) => page.evaluate(([t, top]) => window.__cap(t, top), [t, top]);
const wait = (ms) => page.waitForTimeout(ms);

async function card(title, lines, ms) {
  await page.setContent(`<html><body style="margin:0;height:100vh;display:flex;flex-direction:column;justify-content:center;
    align-items:center;background:#0f172a;color:#e2e8f0;font-family:'Malgun Gothic','Noto Sans KR',sans-serif;text-align:center">
    <div style="font-size:64px;font-weight:700;color:#fff">${title}</div>
    <div style="margin-top:28px;font-size:28px;line-height:1.8">${lines.join('<br>')}</div></body></html>`);
  await wait(ms);
}

async function typeSlow(sel, text) {
  await page.click(sel);
  await page.keyboard.type(text, { delay: 70 });
}

async function waitAnswer(n) {
  // n번째 답변의 근거가 붙을 때까지 (LLM 응답 대기)
  await page.waitForFunction((n) => document.querySelectorAll('#log .msg .answer').length >= n ||
    document.querySelectorAll('#log .msg.error').length > 0, n, { timeout: 90000 });
}

async function showEvidence(n) {
  const det = page.locator('#log details').nth(n - 1);
  await det.scrollIntoViewIfNeeded();
  await det.locator('summary').click();
  await wait(600);
  await det.evaluate((d) => d.scrollIntoView({ behavior: 'smooth', block: 'start' }));
}

// 1. 시작 (전체 화면 안내가 사라진 뒤 rec.sh에 녹화 시작 신호)
const TITLE = ['mini-mes', ['제조 설비 생산실적 · OEE · 품질 이력 · 이상탐지 미니 MES',
  '<span style="color:#93c5fd">https://mes.pmhllll12.cloud</span>',
  '<span style="font-size:22px;color:#94a3b8">FastAPI · PostgreSQL · K3s/Helm · Prometheus/Grafana · Isolation Forest · LLM function calling</span>']];
await card(...TITLE, 4000);
fs.writeFileSync('ready', '');
await wait(5000);

// 2. 자연어 질의
await page.goto(`${B}/chat`);
await wait(800);
await cap('자연어 질의: 설비·OEE·불량·이상탐지 데이터를 말로 묻습니다\nLLM은 SQL을 만들지 않고, 검증된 읽기 전용 도구만 호출합니다', true);
await wait(4500);
await cap('예시 질문을 눌러 봅니다', true);
await wait(1500);
await page.getByRole('button', { name: Q1 }).click();
await cap('Gemini가 도구를 고르고, API가 DB를 조회합니다', true);
await waitAnswer(1);
await wait(800);
await cap('답변 아래에 근거(호출한 도구 · 인자 · 결과)가 함께 나옵니다', true);
await wait(3000);
await showEvidence(1);
await wait(4500);

await cap('직접 입력도 됩니다', true);
await typeSlow('#q', Q2);
await wait(500);
await page.keyboard.press('Enter');
await cap('불량은 "건수"와 "수량"을 구분해 답하도록 도구 결과에 단위를 붙였습니다', true);
await waitAnswer(2);
await wait(3500);
await showEvidence(2);
await wait(4500);
await cap('하루 질문 수 · IP당 요청 빈도를 제한해 무료 LLM 한도를 지킵니다', true);
await page.locator('#quota').scrollIntoViewIfNeeded();
await wait(4000);

// 3. Grafana
await cap('');
await page.goto(`${B}/grafana/d/mini-mes-overview?orgId=1&from=now-3h&to=now&kiosk`);
await cap('Grafana 대시보드: 시뮬레이터가 1분마다 보내는 설비 3대의 데이터');
await wait(7000);
await cap('설비별 OEE · 가동률 · 양품률, 불량 유형별 수량');
await wait(4000);
for (const [label, y] of [['API 요청 수 · 지연시간 (Prometheus)', 700], ['이상 점수(점선 = 설비별 threshold) · 열화 점수와 경보 상태', 1400], ['자연어 질의 요청 · 도구 호출 지표', 2100]]) {
  await cap(label);
  await page.mouse.move(W / 2, H / 2);
  await page.mouse.wheel(0, 700);
  await wait(4500);
}

// 4. API 문서
await cap('');
await page.goto(`${B}/docs`);
await page.waitForSelector('.opblock', { timeout: 30000 });
await cap('API: 화면 조작 없이 조건을 지정해 여러 설비를 한 번에 조회 · CSV로 바로 내보내기');
await wait(4000);
const oee = page.locator('#operations-default-oee_all_oee_get, .opblock-get:has(.opblock-summary-path[data-path="/oee"])').first();
await oee.scrollIntoViewIfNeeded();
await oee.locator('.opblock-summary').click();
await wait(1200);
await page.getByRole('button', { name: 'Try it out' }).click();
await wait(800);
await page.getByRole('button', { name: 'Execute' }).click();
await cap('GET /oee — 전체 설비 OEE를 한 번에');
await wait(2000);
await page.locator('.live-responses-table .response-col_description').first().evaluate((e) => e.scrollIntoView({ behavior: 'smooth', block: 'start' }));
await wait(5000);
await cap('조회는 공개, 데이터 등록·관리 요청은 인증으로 잠가 두었습니다');
await wait(4000);

// 5. 마무리
await cap('');
await card('구성', [
  'GitHub Actions → GHCR (amd64/arm64) → Helm 차트로 K3s 배포',
  '이상탐지: 설비별 Isolation Forest + robust z-score, 급변·열화 경보 분리',
  'Terraform(Oracle Cloud) · 지금은 개인 PC k3d + Cloudflare Tunnel로 임시 공개',
  '<span style="color:#93c5fd">github.com/pmhllll12/mini-mes</span>'], 7000);

fs.writeFileSync('stop', '');
await wait(500);
await ctx.close();
fs.rmSync(profile, { recursive: true, force: true });
