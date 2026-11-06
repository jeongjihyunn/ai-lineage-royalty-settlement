import { useEffect, useRef, useState } from "react";
import { AnimatePresence, MotionConfig, animate, motion, useInView } from "framer-motion";

const STATUS = {
  APPROVED: { label: "정산 가능", tone: "green", desc: "라이선스·지갑 모두 확인됨" },
  APPROVED_WITH_ESCROW: { label: "일부 보관 후 정산", tone: "orange", desc: "지갑 못 찾은 재료의 사용료만 보관" },
  HOLD: { label: "정산 보류", tone: "red", desc: "상업적 사용 불가 재료 있음" },
  REVIEW: { label: "사람 검토 필요", tone: "yellow", desc: "근거 부족 · 담당자 확인 필요" },
  TAMPERED: { label: "원본과 다른 파일", tone: "red", desc: "등록된 해시와 일치하지 않음" },
};
const ROLES = {
  base_model: { label: "베이스 모델", color: "var(--purple)" },
  dataset: { label: "데이터셋", color: "var(--cyan)" },
  adapter: { label: "어댑터", color: "var(--yellow)" },
};
const STEPS = ["모델 정보 읽기", "정산 판정", "사람 승인", "블록체인 등록", "사용료 입금", "권리자 수령"];
const APPROVABLE = ["APPROVED", "APPROVED_WITH_ESCROW"];
const ALREADY = "ALREADY_REGISTERED";
const OK_REASON = "라이선스·근거·수령 지갑 확인됨";
const ease = [0.22, 1, 0.36, 1];

const shortWallet = (w) => (w && w.length > 12 ? `${w.slice(0, 6)}…${w.slice(-4)}` : w || "");
const role = (r) => ROLES[r] || { label: r, color: "var(--mute)" };
// wei 단위 잔돈은 소수점 18자리까지 길어지므로 줄여 보여준다.
const shortEth = (v) => (Number(v) > 0 && Number(v) < 0.000001 ? "0.000001 미만" : v);

// 정책 엔진 문구를 처음 보는 사람도 알아듣게 바꿔 보여준다. 모르는 문구는 그대로 둔다.
const PLAIN_MESSAGES = [
  [/^수령 지갑 미확인/, () => "지갑 못 찾음 · 사용료 보관"],
  [/^상업 이용 금지 라이선스\((.*)\)$/, (m) => `상업적 사용 불가 (${m[1]})`],
  [/^라이선스 불명\(값 없음\)$/, () => "라이선스 표기 없음"],
  [/^라이선스 불명\((.*)\)$/, (m) => `알 수 없는 라이선스 (${m[1]})`],
  [/^근거 인용 없음$/, () => "근거 문장 없음"],
  [/^재료 이름 없음$/, () => "재료 이름 없음"],
  [/^재료 목록 없음$/, () => "재료 못 찾음"],
  [/^비율 규칙이 없는 역할\((.*)\)$/, (m) => `비율 미정 재료 (${m[1]})`],
  [/^선언 라이선스와 상위 라이선스 불일치$/, () => "원본 모델과 라이선스 다름"],
  [/^선언·상위 라이선스 불일치 여부 미확인$/, () => "원본 모델과 라이선스 비교 못 함"],
];
function plainMessage(message) {
  for (const [pattern, write] of PLAIN_MESSAGES) {
    const m = message.match(pattern);
    if (m) return write(m);
  }
  return message;
}
function plainTarget(target) {
  if (target === "license_analysis") return "라이선스 비교";
  if (target === "선언 라이선스") return "이 모델이 밝힌 라이선스";
  const [first, ...rest] = target.split(" ");
  return ROLES[first] ? `${ROLES[first].label} · ${rest.join(" ")}` : target;
}

// 블록체인 함수가 돌려주는 오류 코드를 짧은 말로 바꾼다. 원문은 마우스를 올리면 보인다.
const SETUP_ERROR = "서버 설정 확인 필요";
const ERROR_TEXT = {
  NOT_APPROVED: "입금 거부 · 승인되지 않은 모델",
  MODEL_HASH_NOT_REGISTERED: "입금 거부 · 등록되지 않은 모델",
  NOT_REGISTERED: "등록되지 않은 모델",
  INVALID_AMOUNT: "입금액 형식 오류",
  NOTHING_TO_CLAIM: "받을 금액 없음",
  INSUFFICIENT_ESCROW: "보관금 부족",
  INVALID_HOLDER: "받는 사람 주소 오류",
  INVALID_RECIPIENT: "받는 사람 주소 오류",
  INVALID_CASE: "모델 정보 파일 형식 오류",
  NETWORK_ERROR: "블록체인 연결 실패 · 다시 시도",
  SIMULATION_FAILED: "전송 전 확인 실패 · 가스비 잔액 확인",
  SEND_FAILED: "전송 실패 · 가스비 잔액 확인",
  TX_PENDING: "확정 대기 중 · Etherscan에서 확인",
  TX_FAILED: "거래 실패 · Etherscan에서 확인",
  ONLY_OPERATOR: SETUP_ERROR,
  ONLY_REVIEWER: SETUP_ERROR,
  HOLDER_KEY_MISMATCH: SETUP_ERROR,
  MISSING_CONFIG: SETUP_ERROR,
  INVALID_KEY: SETUP_ERROR,
  WRONG_NETWORK: SETUP_ERROR,
  INVALID_CONTRACT_ADDRESS: SETUP_ERROR,
  CONTRACT_NOT_FOUND: SETUP_ERROR,
  WEB3_NOT_INSTALLED: SETUP_ERROR,
  INVALID_WALLET_MAP: SETUP_ERROR,
};
const errorText = (result) => ERROR_TEXT[result.error_code] || result.error_message || "요청 실패";

async function fetchJson(url, options) {
  const res = await fetch(url, options).catch(() => {
    throw new Error("서버 연결 실패");
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(typeof body.detail === "string" ? body.detail : "요청 실패");
  return body;
}

export default function App() {
  const [examples, setExamples] = useState([]);
  const [source, setSource] = useState(null); // { kind: "example", id } | { kind: "file", name, text }
  const [review, setReview] = useState(null);
  const [error, setError] = useState(null);
  const [upload, setUpload] = useState(null); // { name, state: "loading" | "done" | "error" }
  const [registration, setRegistration] = useState(null);
  const [progress, setProgress] = useState({}); // 체인 등록·입금·수령 여부. 다른 예제의 늦은 응답은 key로 거른다.

  useEffect(() => {
    fetchJson("/api/examples")
      .then((list) => {
        setExamples(list);
        if (list[0]) pickExample(list[0].id);
      })
      .catch((e) => setError(e.message));
  }, []);

  async function load(promise, nextSource) {
    setError(null);
    setRegistration(null);
    try {
      const data = await promise;
      setReview(data);
      setSource(nextSource);
      return true;
    } catch (e) {
      setError(e.message);
      return false;
    }
  }

  function pickExample(id) {
    setUpload(null);
    load(fetchJson(`/api/examples/${id}`), { kind: "example", id });
  }

  async function pickFile(file) {
    if (!file) return;
    setUpload({ name: file.name, size: file.size, state: "loading" });
    const text = await file.text();
    const ok = await load(
      fetchJson("/api/review", { method: "POST", headers: { "Content-Type": "application/json" }, body: text }),
      { kind: "file", name: file.name, text },
    );
    setUpload({ name: file.name, size: file.size, state: ok ? "done" : "error" });
  }

  const key = source ? `${source.kind}:${source.id || source.name}` : "none";
  const status = review?.result.status;
  const chain = progress.key === key ? progress : {};
  const registered = registration?.ok || registration?.error_code === ALREADY || (review?.register.ok && chain.registered);
  const stepStates = !review
    ? STEPS.map(() => "")
    : [
        "done",
        "done",
        review.register.ok ? (registered ? "done" : "now") : "blocked",
        registered ? "done" : "",
        registered ? (chain.deposited ? "done" : "now") : "",
        registered && chain.deposited ? (chain.claimed ? "done" : "now") : "",
      ];
  const locked = APPROVABLE.includes(status) && !review?.tamper && !registered;

  return (
    <MotionConfig reducedMotion="user">
      <main>
        <motion.header initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6, ease }}>
          <h1>AI 모델 사용료 정산</h1>
          <p>AI 모델을 만든 재료의 권리자에게 사용료를 나눠도 되는지 판정하고, 승인하면 분배 규칙을 블록체인에 등록합니다.</p>
        </motion.header>

        <Input
          examples={examples}
          source={source}
          upload={upload}
          onExample={pickExample}
          onFile={pickFile}
        />

        <Steps states={stepStates} />

        {error && <div className="error">{error}</div>}

        <AnimatePresence mode="wait">
          {review && (
            <motion.div
              key={key}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0, filter: "blur(6px)" }}
              transition={{ duration: 0.25 }}
            >
              <Verdict status={review.tamper ? "TAMPERED" : status} modelId={review.model.id} />
              <Lineage review={review} />
              {review.tamper && <HashCheck tamper={review.tamper} />}
              {APPROVABLE.includes(status) && !review.tamper && <Shares result={review.result} />}
              <Approve register={review.register} source={source} registration={registration} onChain={chain.registered} onResult={setRegistration} />
              <Payment review={review} source={source} registration={registration} locked={locked} onProgress={(p) => setProgress({ key, ...p })} />
              {status !== "APPROVED" && <Reasons reasons={review.result.reasons} escrow={status === "APPROVED_WITH_ESCROW"} />}
            </motion.div>
          )}
        </AnimatePresence>
      </main>
    </MotionConfig>
  );
}

function Input({ examples, source, upload, onExample, onFile }) {
  const fileRef = useRef(null);
  const [over, setOver] = useState(false);

  return (
    <section className="input glass">
      <div
        className={`drop ${over ? "over" : ""}`}
        role="button"
        tabIndex={0}
        onClick={() => fileRef.current?.click()}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && fileRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          onFile(e.dataTransfer.files[0]);
        }}
      >
        <input
          ref={fileRef}
          type="file"
          accept=".json,application/json"
          hidden
          onChange={(e) => {
            onFile(e.target.files[0]);
            e.target.value = "";
          }}
        />
        <AnimatePresence mode="wait">
          {upload ? (
            <UploadRow key={upload.name + upload.state} upload={upload} />
          ) : (
            <motion.div key="empty" className="drop-empty" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}>
              <span className="drop-icon">↑</span>
              <strong>모델 정보 파일을 끌어다 놓기</strong>
              <span>또는 클릭해서 JSON 파일 고르기</span>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <div className="examples">
        <span className="examples-title" id="example-label">팀이 만든 예제</span>
        <ExampleSelect examples={examples} value={source?.kind === "example" ? source.id : null} onChange={onExample} />
      </div>
    </section>
  );
}

function ExampleSelect({ examples, value, onChange }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  const current = examples.find((ex) => ex.id === value);

  useEffect(() => {
    if (!open) return;
    const close = (e) => !ref.current?.contains(e.target) && setOpen(false);
    const esc = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  return (
    <div className="select" ref={ref}>
      <button
        className={`select-button ${open ? "open" : ""}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-labelledby="example-label"
        onClick={() => setOpen((o) => !o)}
      >
        <span className={current ? "" : "placeholder"}>{current ? current.label : "예제 고르기"}</span>
        <motion.span className="chevron" animate={{ rotate: open ? 180 : 0 }} transition={{ duration: 0.2 }}>
          ⌄
        </motion.span>
      </button>
      <AnimatePresence>
        {open && (
          <motion.ul
            className="select-menu"
            role="listbox"
            aria-labelledby="example-label"
            initial={{ opacity: 0, y: -6, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.98 }}
            transition={{ duration: 0.16, ease }}
          >
            {examples.map((ex) => (
              <li key={ex.id} role="option" aria-selected={ex.id === value}>
                <button
                  className={`select-option ${ex.id === value ? "selected" : ""}`}
                  onClick={() => {
                    setOpen(false);
                    onChange(ex.id);
                  }}
                >
                  {ex.label}
                  {ex.id === value && <span className="check">✓</span>}
                </button>
              </li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </div>
  );
}

function UploadRow({ upload }) {
  const done = upload.state === "done";
  const failed = upload.state === "error";
  return (
    <motion.div className="upload-row" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}>
      <span className="file-icon">{"{ }"}</span>
      <div className="upload-main">
        <div className="upload-name">{upload.name}</div>
        <div className="upload-sub">{(upload.size / 1024).toFixed(1)} KB</div>
        <div className="track">
          <motion.div
            className={`fill ${failed ? "failed" : ""}`}
            initial={{ scaleX: 0 }}
            animate={{ scaleX: upload.state === "loading" ? 0.85 : 1 }}
            transition={{ duration: upload.state === "loading" ? 1.2 : 0.3, ease }}
          />
        </div>
      </div>
      <AnimatePresence>
        {(done || failed) && (
          <motion.span
            className={`upload-badge ${failed ? "failed" : ""}`}
            initial={{ scale: 0 }}
            animate={{ scale: 1 }}
            transition={{ type: "spring", stiffness: 420, damping: 18, delay: 0.25 }}
          >
            {failed ? "!" : "✓"}
          </motion.span>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

function Steps({ states }) {
  // 선은 마지막으로 끝났거나 진행 중인 단계까지만 채운다.
  const reached = Math.max(0, ...states.map((s, i) => (s === "done" || s === "now" ? i : 0)));
  return (
    <ol className="steps">
      <motion.div
        className="steps-line"
        initial={false}
        animate={{ scaleX: reached / (STEPS.length - 1) }}
        transition={{ duration: 0.8, ease }}
      />
      {STEPS.map((title, i) => (
        <li key={title} className={`step ${states[i]}`}>
          <motion.span
            className="dot"
            initial={false}
            animate={{ scale: states[i] === "now" ? [1, 1.18, 1] : 1 }}
            transition={states[i] === "now" ? { duration: 1.6, repeat: Infinity } : { duration: 0.3 }}
          >
            {states[i] === "done" ? "✓" : states[i] === "blocked" ? "!" : i + 1}
          </motion.span>
          <span className="step-title">{title}</span>
        </li>
      ))}
    </ol>
  );
}

function Verdict({ status, modelId }) {
  const view = STATUS[status];
  return (
    <motion.section
      className={`verdict tone-${view.tone}`}
      initial={{ opacity: 0, scale: 0.96, filter: "blur(10px)" }}
      animate={{ opacity: 1, scale: 1, filter: "blur(0px)" }}
      transition={{ duration: 0.6, ease }}
    >
      <span className="border-beam" aria-hidden="true" />
      <div className="verdict-label">{view.label}</div>
      <div className="verdict-desc">{view.desc}</div>
      <div className="verdict-model">{modelId}</div>
    </motion.section>
  );
}

const PART_H = 76;

function Lineage({ review }) {
  const wallets = Object.fromEntries(review.result.recipients.map((r) => [`${r.role}|${r.name}`, r.wallet]));
  const approvable = APPROVABLE.includes(review.result.status);
  const parts = review.components;
  // 정책 엔진이 재료에 붙인 문제(확인됨 제외)를 재료별로 모은다.
  const issues = {};
  for (const r of review.result.reasons || []) {
    if (r.message === OK_REASON) continue;
    (issues[r.target] ||= []).push(plainMessage(r.message));
  }

  return (
    <section className="block">
      <h2>이 모델을 만든 재료</h2>
      <div className="lineage">
        <div className="parts">
          {parts.map((c, i) => {
            const r = role(c.role);
            const wallet = wallets[`${c.role}|${c.name}`];
            const problems = issues[`${c.role} ${c.name}`];
            return (
              <motion.div
                key={`${c.role}-${c.name}`}
                className="part glass"
                style={{ "--accent": r.color, height: PART_H }}
                initial={{ opacity: 0, x: -24 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.15 + i * 0.12, duration: 0.5, ease }}
              >
                <span className="part-role">{r.label}</span>
                <span className="part-name">{c.name}</span>
                <span className="part-meta">
                  <code>{c.license || "license ?"}</code>
                  {approvable && (wallet ? <span className="wallet">{shortWallet(wallet)}</span> : <span className="warn">지갑 미확인</span>)}
                </span>
                {problems && (
                  <span className={`issue ${review.result.status === "APPROVED_WITH_ESCROW" ? "escrow" : ""}`} tabIndex={0} aria-label={problems.join(", ")}>
                    !
                    <span className="issue-tip" role="tooltip">
                      {problems.map((m) => <span key={m}>{m}</span>)}
                    </span>
                  </span>
                )}
              </motion.div>
            );
          })}
        </div>

      </div>
    </section>
  );
}

function Shares({ result }) {
  const rows = result.recipients.map((r) => ({
    key: `${r.role}-${r.name}`,
    label: `${role(r.role).label} · ${r.name}`,
    color: role(r.role).color,
    wallet: shortWallet(r.wallet),
    pct: r.share_bps / 100,
  }));
  if (result.escrow_bps) {
    rows.push({ key: "escrow", label: "따로 보관 · 지갑을 찾으면 지급", color: "var(--mute)", wallet: "—", pct: result.escrow_bps / 100, escrow: true });
  }

  // 화면에 보일 때 왼쪽부터 한 칸씩 차오른다.
  const ref = useRef(null);
  const seen = useInView(ref, { once: true, amount: 0.6 });
  const [hover, setHover] = useState(null);

  return (
    <section className="block">
      <h2>사용료 분배</h2>
      <div className="bar" ref={ref}>
        {rows.map((r, i) => (
          <motion.div
            key={r.key}
            className={`seg ${r.escrow ? "escrow" : ""} ${hover && hover !== r.key ? "dim" : ""}`}
            style={{ background: r.escrow ? undefined : r.color }}
            onMouseEnter={() => setHover(r.key)}
            onMouseLeave={() => setHover(null)}
            initial={{ width: "0%" }}
            animate={seen ? { width: `${r.pct}%` } : undefined}
            transition={{ delay: i * 0.45, duration: 0.7, ease }}
          >
            <motion.span
              initial={{ opacity: 0, y: 6 }}
              animate={seen ? { opacity: 1, y: 0 } : undefined}
              transition={{ delay: i * 0.45 + 0.45, duration: 0.4, ease }}
            >
              {r.pct}%
            </motion.span>
          </motion.div>
        ))}
      </div>
      <ul className="legend">
        {rows.map((r) => (
          <li
            key={r.key}
            className={hover === r.key ? "on" : hover ? "dim" : ""}
            onMouseEnter={() => setHover(r.key)}
            onMouseLeave={() => setHover(null)}
          >
            <i style={{ background: r.color }} />
            <span className="legend-label">{r.label}</span>
            <span className="wallet">{r.wallet}</span>
            <strong><Ticker value={r.pct} start={seen} /></strong>
          </li>
        ))}
      </ul>
    </section>
  );
}

// 0에서 값까지 숫자가 올라간다 (Magic UI Number Ticker 아이디어).
function Ticker({ value, start = true }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!start) return;
    const controls = animate(0, value, {
      duration: 1.2,
      ease,
      onUpdate: (v) => ref.current && (ref.current.textContent = Math.round(v)),
    });
    return () => controls.stop();
  }, [value, start]);
  return <><span ref={ref}>0</span>%</>;
}

function HashCheck({ tamper }) {
  return (
    <section className="block">
      <h2>파일 해시 비교</h2>
      <dl className="hashes">
        <div>
          <dt>등록된 원본</dt>
          <dd>{tamper.original}</dd>
        </div>
        <div className="mismatch">
          <dt>입금하려는 파일</dt>
          <dd>{tamper.submitted}</dd>
        </div>
      </dl>
    </section>
  );
}

function Approve({ register, source, registration, onChain, onResult }) {
  const [pending, setPending] = useState(false);

  async function click() {
    setPending(true);
    onResult(null);
    try {
      const request = source.kind === "example"
        ? fetchJson(`/api/examples/${source.id}/register`, { method: "POST" })
        : fetchJson("/api/register", { method: "POST", headers: { "Content-Type": "application/json" }, body: source.text });
      onResult(await request);
    } catch (e) {
      onResult({ ok: false, error_message: e.message });
    } finally {
      setPending(false);
    }
  }

  const already = registration?.error_code === ALREADY || (!registration && onChain);
  const done = registration?.ok || already;

  return (
    <section className="block">
      <h2>사람 승인</h2>
      {!register.ok ? (
        <p className="muted">{register.reason}</p>
      ) : (
        <div className="approve">
          <motion.button
            className="primary"
            whileHover={{ y: -2 }}
            whileTap={{ scale: 0.97 }}
            disabled={pending || done}
            onClick={click}
          >
            {pending ? <span className="spinner" /> : already ? "이미 등록됨" : done ? "등록 완료" : "승인하고 블록체인에 등록"}
          </motion.button>
          <AnimatePresence>
            {registration && (
              <motion.div className="result" aria-live="polite" initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }}>
                {!already && !registration.ok && <p className="notice" title={registration.error_message}>{errorText(registration)}</p>}
                {already && registration.evidence_changed && <p className="notice">등록 당시와 근거 파일이 다름</p>}
                {already && registration.contract_address && (
                  <a href={`https://sepolia.etherscan.io/address/${registration.contract_address}`} target="_blank" rel="noreferrer">
                    Etherscan에서 컨트랙트 보기
                  </a>
                )}
                {registration.etherscan_url && (
                  <a href={registration.etherscan_url} target="_blank" rel="noreferrer">
                    Etherscan에서 보기 ({shortWallet(registration.tx_hash)})
                  </a>
                )}
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      )}
    </section>
  );
}

function TxResult({ result, done }) {
  if (!result) return null;
  return (
    <div className="result" aria-live="polite">
      {result.ok
        ? <p className="tx-done">{done}</p>
        : <p className="notice" title={result.error_message}>{errorText(result)}</p>}
      {result.etherscan_url && (
        <a href={result.etherscan_url} target="_blank" rel="noreferrer">
          Etherscan에서 보기 ({shortWallet(result.tx_hash)})
        </a>
      )}
    </div>
  );
}

function Payment({ review, source, registration, locked, onProgress }) {
  const hash = review.tamper ? null : review.model.hash;
  const [amount, setAmount] = useState("0.001");
  const [pending, setPending] = useState(null); // "deposit" | 권리자 지갑 주소
  const [deposited, setDeposited] = useState(null);
  const [claims, setClaims] = useState({});
  const [settlement, setSettlement] = useState(null);
  const [released, setReleased] = useState(null);

  const refresh = () => fetchJson(`/api/settlement/${hash}`).then(setSettlement).catch(() => setSettlement(null));

  useEffect(() => {
    if (hash) refresh();
  }, [hash, registration]);

  // 이번 화면에서 입금했거나 체인에 받을 돈이 남아 있으면 입금 단계는 끝난 것으로 본다.
  useEffect(() => {
    onProgress({
      registered: !!settlement?.registered,
      deposited: !!deposited?.ok || !!settlement?.recipients?.some((r) => r.claimable_eth !== "0") || (settlement?.escrow_eth ?? "0") !== "0",
      claimed: Object.values(claims).some((c) => c?.ok),
    });
  }, [settlement, deposited, claims]);

  async function send() {
    setPending("deposit");
    setDeposited(null);
    const query = `amount_eth=${encodeURIComponent(amount)}`;
    try {
      const request = source.kind === "example"
        ? fetchJson(`/api/examples/${source.id}/deposit?${query}`, { method: "POST" })
        : fetchJson(`/api/deposit?${query}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: source.text });
      setDeposited(await request);
      if (hash) await refresh();
    } catch (e) {
      setDeposited({ ok: false, error_message: e.message });
    } finally {
      setPending(null);
    }
  }

  async function claim(row) {
    setPending(row.wallet);
    setClaims((c) => ({ ...c, [row.wallet]: null }));
    try {
      const result = await fetchJson(`/api/settlement/${hash}/claim/${row.holder}`, { method: "POST" });
      setClaims((c) => ({ ...c, [row.wallet]: result }));
      await refresh();
    } catch (e) {
      setClaims((c) => ({ ...c, [row.wallet]: { ok: false, error_message: e.message } }));
    } finally {
      setPending(null);
    }
  }

  async function release() {
    setPending("release");
    setReleased(null);
    try {
      setReleased(await fetchJson(`/api/examples/${source.id}/release`, { method: "POST" }));
      await refresh();
    } catch (e) {
      setReleased({ ok: false, error_message: e.message });
    } finally {
      setPending(null);
    }
  }

  const names = Object.fromEntries(review.result.recipients.map((r) => [r.wallet?.toLowerCase(), r]));

  return (
    <>
      <section className={`block ${locked ? "locked" : ""}`}>
        <h2>사용료 입금</h2>
        <div className="approve">
          <label className="amount">
            <span className="sr-only">입금액</span>
            <input inputMode="decimal" value={amount} disabled={locked} onChange={(e) => setAmount(e.target.value)} />
            <span>ETH</span>
          </label>
          <button className="primary deposit" disabled={locked || !!pending || !amount} onClick={send}>
            {pending === "deposit" ? <span className="spinner" /> : "입금"}
          </button>
          <TxResult result={deposited} done="입금 완료" />
        </div>
      </section>

      {settlement?.registered && (
        <section className="block">
          <h2>권리자 수령</h2>
          <ul className="claims">
            {settlement.recipients.map((row) => {
              const info = names[row.wallet.toLowerCase()];
              return (
                <li key={row.wallet}>
                  <i style={{ background: role(info?.role).color }} />
                  <span className="legend-label">{info ? `${role(info.role).label} · ${info.name}` : `보관금 수령 · ${row.holder}`}</span>
                  <span className="wallet">{shortWallet(row.wallet)}</span>
                  <strong title={`${row.claimable_eth} ETH`}>{shortEth(row.claimable_eth)} ETH</strong>
                  <button
                    className="claim"
                    disabled={!!pending || !row.holder || row.claimable_eth === "0"}
                    onClick={() => claim(row)}
                  >
                    {pending === row.wallet ? <span className="spinner dark" /> : "받기"}
                  </button>
                  {claims[row.wallet] && <div className="claim-result"><TxResult result={claims[row.wallet]} done="수령 완료" /></div>}
                </li>
              );
            })}
            {settlement.escrow_eth !== "0" && (
              <li>
                <i style={{ background: "var(--mute)" }} />
                <span className="legend-label">따로 보관{review.escrow_holder && ` · ${review.escrow_holder}에게 지급 예정`}</span>
                <span className="wallet">—</span>
                <strong title={`${settlement.escrow_eth} ETH`}>{shortEth(settlement.escrow_eth)} ETH</strong>
                {review.escrow_holder ? (
                  <button className="claim" disabled={!!pending} onClick={release}>
                    {pending === "release" ? <span className="spinner dark" /> : "지급"}
                  </button>
                ) : <span />}
              </li>
            )}
          </ul>
          <TxResult result={released} done="보관금 지급 완료" />
        </section>
      )}
    </>
  );
}

function Reasons({ reasons, escrow }) {
  return (
    <section className="block">
      <h2>판정 근거</h2>
      {reasons.map((r, i) => (
        <motion.article
          key={i}
          className={`reason glass ${escrow ? "escrow" : ""}`}
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 + i * 0.08, duration: 0.45, ease }}
        >
          {r.target && <div className="reason-target">{plainTarget(r.target)}</div>}
          <div className="reason-msg">
            <span className="mark" aria-hidden="true">!</span>
            {plainMessage(r.message)}
          </div>
          {(r.evidence || []).map((ev, j) => (
            <blockquote key={j}>
              “{ev.evidence_quote}”
              <small>{[ev.evidence_location, ev.source_url].filter(Boolean).join(" · ")}</small>
            </blockquote>
          ))}
        </motion.article>
      ))}
    </section>
  );
}
