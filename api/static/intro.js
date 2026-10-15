// 채팅 화면 인트로 - "시스템 부팅 로그" 오버레이 (약 4초, 세션당 1회).
// chat.html의 <body> 바로 뒤에서 동기 로드해 본 화면이 그려지기 전에 덮는다. 기존 화면·스크립트는 건드리지 않는다.
// ?intro=1이면 강제 재생, prefers-reduced-motion: reduce면 재생하지 않음. 클릭·터치·아무 키나 누르면 즉시 종료.
(() => {
  // ---- 타이밍(ms) - 여기만 바꾸면 된다 ----
  const START_DELAY_MS = 150;   // 첫 줄 타이핑 시작 전 대기
  const CHAR_MS = 9;            // 글자당 타이핑 시간
  const STATUS_DELAY_MS = 110;  // 줄 타이핑이 끝난 뒤 OK/✓가 붙기까지
  const LINE_GAP_MS = 40;       // OK/✓ 뒤 다음 줄 시작까지
  const LOG_END_MS = 2400;      // 1단계 끝: 로그가 흐려지고 로고 등장
  const LOGO_END_MS = 3300;     // 2단계 끝: 오버레이가 페이지 배경색으로 바뀌며 사라지기 시작
  const FADE_END_MS = 4000;     // 3단계 끝: 오버레이 제거, 입력창 포커스

  const LINES = [
    ["connecting equipment EQ-001 ~ EQ-003 ... ", "OK"],
    ["loading OEE / defect / anomaly data ... ", "OK"],
    ["KAMP precision dataset ... ", "OK"],
    ["LLM guardrail: read-only tools only ", "✓"],
  ];
  const STORAGE_KEY = "mini-mes:intro-played";

  const forced = new URLSearchParams(location.search).get("intro") === "1";
  if (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  try {
    if (!forced && sessionStorage.getItem(STORAGE_KEY)) return;
    sessionStorage.setItem(STORAGE_KEY, "1");
  } catch (e) { /* 저장소를 못 쓰면 매번 재생 */ }

  const root = document.documentElement;
  const overlay = document.createElement("div");
  overlay.className = "intro";
  overlay.setAttribute("aria-hidden", "true");
  overlay.style.setProperty("--intro-logo-ms", `${LOGO_END_MS - LOG_END_MS}ms`);
  overlay.style.setProperty("--intro-fade-ms", `${FADE_END_MS - LOGO_END_MS}ms`);
  overlay.innerHTML =
    '<pre class="intro-log"></pre>' +
    '<div class="intro-logo"><div class="intro-title">mini<span>-mes</span></div>' +
    '<div class="intro-sub">Ask your factory.</div></div>' +
    '<div class="intro-skip">Skip ▸</div>';
  const logEl = overlay.querySelector(".intro-log");

  // 줄마다 [시작 시각, 타이핑 끝 시각, OK/✓ 표시 시각]
  let t = START_DELAY_MS;
  const rows = LINES.map(([text, status]) => {
    const el = document.createElement("div");
    el.className = "intro-line";
    el.innerHTML = '<span class="intro-prompt">&gt; </span><span class="intro-text"></span><span class="intro-status"></span>';
    el.querySelector(".intro-status").textContent = status;
    logEl.appendChild(el);
    const row = { el, text, textEl: el.querySelector(".intro-text"), shown: -1,
                  start: t, typed: t + text.length * CHAR_MS };
    row.status = row.typed + STATUS_DELAY_MS;
    t = row.status + LINE_GAP_MS;
    return row;
  });

  let startAt = null, raf = 0, done = false, phase = 0;

  function render(now) {
    if (done) return;
    if (startAt === null) startAt = now;
    const el = now - startAt;
    for (const r of rows) {
      const n = Math.max(0, Math.min(r.text.length, Math.floor((el - r.start) / CHAR_MS)));
      if (el >= r.start && n !== r.shown) { r.textEl.textContent = r.text.slice(0, n); r.shown = n; }
      r.el.classList.toggle("is-active", el >= r.start && el < r.status);
      if (el >= r.start) r.el.classList.add("is-visible");
      if (el >= r.status) r.el.classList.add("is-done");
    }
    if (phase < 1 && el >= LOG_END_MS) { phase = 1; overlay.classList.add("is-logo"); }
    if (phase < 2 && el >= LOGO_END_MS) { phase = 2; overlay.classList.add("is-fading"); }
    if (el >= FADE_END_MS) { finish(); return; }
    raf = requestAnimationFrame(render);
  }

  function finish() {
    if (done) return;
    done = true;
    cancelAnimationFrame(raf);
    window.removeEventListener("keydown", onKey, true);
    overlay.remove();
    root.classList.remove("intro-lock");
    const q = document.getElementById("q");
    if (q) q.focus({ preventScroll: true });
  }

  function onKey(e) {
    // 건너뛰려고 누른 키가 입력창에 들어가거나 Enter로 전송되지 않게 막는다 (Ctrl/⌘ 조합은 브라우저 단축키라 통과)
    if (!e.ctrlKey && !e.metaKey) e.preventDefault();
    finish();
  }

  overlay.addEventListener("pointerdown", (e) => { e.preventDefault(); finish(); });
  window.addEventListener("keydown", onKey, true);
  root.classList.add("intro-lock");
  (document.body || root).prepend(overlay);
  raf = requestAnimationFrame(render);
})();
