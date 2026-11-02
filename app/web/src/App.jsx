import { useEffect, useRef, useState } from "react";
import { AnimatePresence, MotionConfig, animate, motion, useInView } from "framer-motion";

const STATUS = {
  APPROVED: { label: "정산 가능", tone: "green", desc: "모든 재료의 라이선스와 수령 지갑이 확인됐습니다." },
  APPROVED_WITH_ESCROW: { label: "일부 보관 후 정산", tone: "cyan", desc: "지갑이 확인되지 않은 몫은 컨트랙트에 보관하고 나머지를 나눕니다." },
  HOLD: { label: "정산 보류", tone: "red", desc: "상업적 사용이 막힌 재료가 있어 사용료를 나눌 수 없습니다." },
  REVIEW: { label: "사람 검토 필요", tone: "yellow", desc: "근거가 부족하거나 조건이 붙어 있어 담당자가 직접 확인해야 합니다." },
};
const ROLES = {
  base_model: { label: "베이스 모델", color: "var(--purple)" },
  dataset: { label: "데이터셋", color: "var(--cyan)" },
  adapter: { label: "어댑터", color: "var(--yellow)" },
};
const STEPS = ["모델 카드 분석", "정책 판정", "Reviewer 승인", "블록체인 등록"];
const APPROVABLE = ["APPROVED", "APPROVED_WITH_ESCROW"];
const OK_REASON = "라이선스·근거·수령 지갑 확인됨";
const ease = [0.22, 1, 0.36, 1];

const shortWallet = (w) => (w && w.length > 12 ? `${w.slice(0, 6)}…${w.slice(-4)}` : w || "");
const role = (r) => ROLES[r] || { label: r, color: "var(--mute)" };

async function fetchJson(url, options) {
  const res = await fetch(url, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `요청 실패 (${res.status})`);
  return body;
}

export default function App() {
  const [examples, setExamples] = useState([]);
  const [source, setSource] = useState(null); // { kind: "example", id } | { kind: "file", name }
  const [review, setReview] = useState(null);
  const [error, setError] = useState(null);
  const [upload, setUpload] = useState(null); // { name, state: "loading" | "done" | "error" }
  const [approved, setApproved] = useState(false);

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
    setApproved(false);
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
      { kind: "file", name: file.name },
    );
    setUpload({ name: file.name, size: file.size, state: ok ? "done" : "error" });
  }

  const key = source ? `${source.kind}:${source.id || source.name}` : "none";
  const status = review?.result.status;
  const stepStates = !review
    ? ["", "", "", ""]
    : ["done", "done", review.register.ok ? (approved ? "done" : "now") : "blocked", approved ? "now" : ""];

  return (
    <MotionConfig reducedMotion="user">
      <main>
        <motion.header initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.6, ease }}>
          <h1>계보 정산 검토</h1>
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
              <Verdict status={status} modelId={review.model.id} />
              <Lineage review={review} />
              {APPROVABLE.includes(status) && <Shares result={review.result} />}
              <Approve register={review.register} approved={approved} onApprove={() => setApproved(true)} />
              {status !== "APPROVED" && <Reasons reasons={review.result.reasons} />}
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
      <div className="verdict-model">{modelId}</div>
      <div className="verdict-label">{view.label}</div>
      <div className="verdict-desc">{view.desc}</div>
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
    (issues[r.target] ||= []).push(r.message);
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
                  <span className="issue" tabIndex={0} aria-label={problems.join(", ")}>
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
    rows.push({ key: "escrow", label: "에스크로 · 컨트랙트에 보관", color: "var(--mute)", wallet: "—", pct: result.escrow_bps / 100, escrow: true });
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

function Approve({ register, approved, onApprove }) {
  const [pending, setPending] = useState(false);

  function click() {
    setPending(true);
    setTimeout(() => {
      setPending(false);
      onApprove();
    }, 700);
  }

  return (
    <section className="block">
      <h2>Reviewer 승인</h2>
      {!register.ok ? (
        <p className="muted">{register.reason}</p>
      ) : (
        <div className="approve">
          <motion.button
            className="primary"
            whileHover={{ y: -2 }}
            whileTap={{ scale: 0.97 }}
            disabled={pending || approved}
            onClick={click}
          >
            {pending ? <span className="spinner" /> : approved ? "승인 완료" : "승인하고 블록체인에 등록"}
          </motion.button>
          <AnimatePresence>
            {approved && (
              <motion.p className="notice" initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }}>
                블록체인 등록 함수 연결 전입니다.
              </motion.p>
            )}
          </AnimatePresence>
        </div>
      )}
    </section>
  );
}

function Reasons({ reasons }) {
  return (
    <section className="block">
      <h2>판정 근거</h2>
      {reasons.map((r, i) => (
        <motion.article
          key={i}
          className="reason glass"
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.3 + i * 0.08, duration: 0.45, ease }}
        >
          {r.target && <div className="reason-target">{r.target}</div>}
          <div className="reason-msg">
            <span className="mark" aria-hidden="true">!</span>
            {r.message}
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
