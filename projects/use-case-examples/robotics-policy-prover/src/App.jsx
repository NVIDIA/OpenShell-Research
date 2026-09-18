import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  Clock3,
  Gauge,
  Play,
  Radar,
  RefreshCcw,
  ShieldCheck,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import RobotScene from "./RobotScene.jsx";
import { homePosition } from "./scenarios.js";

const DEFAULT_GOAL = "Move the green block into the blue tray.";

const fallbackWorld = {
  goal: DEFAULT_GOAL,
  seed: 42,
  objects: [
    {
      id: "green_vial",
      label: "Green block",
      class_name: "standard",
      color: "#31b66b",
      position: [-0.72, 0.18, -0.46],
      size: [0.18, 0.26, 0.18],
      sorted: false,
    },
    {
      id: "blue_tray",
      label: "Blue tray",
      class_name: "tray",
      color: "#3f7dda",
      position: [0.83, 0.08, 0.28],
      size: [0.62, 0.08, 0.42],
      sorted: true,
    },
  ],
  zones: [
    {
      id: "restricted_zone.alpha",
      label: "Restricted",
      color: "#e44f5e",
      position: [0.15, 0.52, 0.04],
      size: [0.56, 1.04, 0.72],
    },
    {
      id: "caution_zone.human",
      label: "Caution",
      color: "#e8b630",
      position: [-0.12, 0.15, -0.46],
      size: [0.96, 0.3, 0.56],
    },
  ],
  human: {
    position: [1.08, 0.08, -0.72],
    radius: 0.24,
    caution: 0.54,
    distance_m: 1.42,
  },
  metrics: {
    fps: 60,
    decision_count: 0,
    p95_solver_ms: 0,
    task_budget_pct: 84,
    compute_budget_pct: 21,
    actions_left: 3,
    sensor_age_ms: 72,
  },
};

const decisionMeta = {
  allow: { label: "Allowed", className: "decision-allow", icon: CheckCircle2 },
  allow_with_constraints: {
    label: "Constrained",
    className: "decision-constrain",
    icon: ShieldCheck,
  },
  deny: { label: "Denied", className: "decision-deny", icon: AlertTriangle },
  approval_required: { label: "Needs Approval", className: "decision-approval", icon: Clock3 },
  pending: { label: "Ready", className: "decision-pending", icon: Radar },
};

function normalizeStep(event, previous) {
  const decision = event?.decision?.decision ?? previous?.decision ?? "pending";
  let approvedPath = previous?.approvedPath ?? [homePosition];
  if (event && Object.hasOwn(event, "approved_path")) {
    const blocked = decision === "deny" || decision === "approval_required";
    approvedPath = event.approved_path ?? (blocked ? [homePosition] : approvedPath);
  }

  return {
    id: event?.id ?? previous?.id ?? "idle",
    title: event?.summary ?? previous?.title ?? "Click Run Experiment",
    decision,
    action: event?.action ?? previous?.action,
    proposedPath: event?.proposed_path ?? previous?.proposedPath ?? [homePosition],
    approvedPath,
    highlight: event?.highlight ?? previous?.highlight,
    executing:
      event?.kind === "execution_update" &&
      event?.highlight?.kind === "execution" &&
      (event?.approved_path?.length ?? 0) > 1,
  };
}

function DecisionBadge({ decision }) {
  const meta = decisionMeta[decision] ?? decisionMeta.pending;
  const Icon = meta.icon;
  return (
    <div className={`decision-badge ${meta.className}`}>
      <Icon size={18} />
      <span>{meta.label}</span>
    </div>
  );
}

function storyLabel(event) {
  if (event.kind === "agent_plan") return "Agent";
  if (event.kind === "prover_decision") return "Policy prover";
  if (event.kind === "execution_update" || event.kind === "execution_blocked") return "Executor";
  return "World";
}

function storyTone(event) {
  if (event.kind === "agent_plan") return "agent";
  if (event.kind === "prover_decision") return event.decision?.decision ?? "prover";
  if (event.kind === "execution_update" || event.kind === "execution_blocked") return "executor";
  return "world";
}

function compactSummary(event) {
  return event.summary;
}

function sanitizeDecision(decision) {
  if (!decision) return null;
  return {
    decision: decision.decision,
    solver_ms: Number(decision.solver_ms?.toFixed?.(2) ?? decision.solver_ms),
    violations: decision.violations,
    constraints: decision.constraints,
    obligations: decision.obligations,
    counterexample: decision.counterexample,
  };
}

function App() {
  const [seed, setSeed] = useState(42);
  const [agentMode, setAgentMode] = useState("openai");
  const [world, setWorld] = useState(fallbackWorld);
  const [events, setEvents] = useState([]);
  const [step, setStep] = useState(() => normalizeStep(null, null));
  const [playbackSpeed, setPlaybackSpeed] = useState(1);
  const [session, setSession] = useState(null);
  const sourceRef = useRef(null);

  const storyEvents = useMemo(
    () => events.filter((event) => ["agent_plan", "prover_decision", "world_event", "execution_update", "execution_blocked"].includes(event.kind)),
    [events],
  );
  const latestDecision = step.decision ?? "pending";
  const latestDecisionEvent = [...events].reverse().find((event) => event.decision);

  const attachEventSource = useCallback((sessionId) => {
    sourceRef.current?.close();
    const source = new EventSource(`/api/sessions/${sessionId}/events`);
    sourceRef.current = source;

    const handle = (message) => {
      const event = JSON.parse(message.data);
      setEvents((items) => [...items.slice(-40), event]);
      if (event.world) setWorld(event.world);
      setStep((previous) => normalizeStep(event, previous));
    };

    ["world_event", "agent_plan", "prover_decision", "execution_update", "execution_blocked", "error"].forEach((kind) =>
      source.addEventListener(kind, handle),
    );
  }, []);

  const startExperiment = useCallback(async () => {
    setEvents([]);
    setStep(normalizeStep(null, null));
    const response = await fetch("/api/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goal: DEFAULT_GOAL, seed: Number(seed), agent_mode: agentMode }),
    });
    const data = await response.json();
    setSession(data);
    setAgentMode(data.agent_mode);
    attachEventSource(data.session_id);
  }, [agentMode, attachEventSource, seed]);

  useEffect(() => () => sourceRef.current?.close(), []);

  return (
    <main className="app-shell simple-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark">
            <ShieldCheck size={22} strokeWidth={2.4} />
          </div>
          <div>
            <h1>Agent Path vs Policy Prover</h1>
            <p>One tool-path experiment: propose, prove, adapt, execute.</p>
          </div>
        </div>

        <div className="topbar-actions">
          <label className="seed-control">
            <span>Seed</span>
            <input
              aria-label="Experiment seed"
              type="number"
              value={seed}
              onChange={(event) => setSeed(event.target.value)}
            />
          </label>
          <label className="seed-control">
            <span>Agent</span>
            <select value={agentMode} onChange={(event) => setAgentMode(event.target.value)}>
              <option value="openai">OpenAI-compatible</option>
              <option value="fixture">Fixture</option>
            </select>
          </label>
          <button className="run-button" type="button" onClick={startExperiment}>
            <Play size={18} />
            <span>Run Experiment</span>
          </button>
          <button className="icon-button" type="button" title="New seed" onClick={() => setSeed((value) => Number(value) + 1)}>
            <RefreshCcw size={18} />
          </button>
        </div>
      </header>

      <section className="simple-layout">
        <section className="scene-panel" aria-label="Robot workcell visualization">
          <div className="scene-status">
            <DecisionBadge decision={latestDecision} />
            <span className="scene-title">{step.title}</span>
          </div>
          <RobotScene world={world} step={step} playbackSpeed={playbackSpeed} />
          <div className="legend-strip">
            <span><i className="legend-dot proposed" />Agent path</span>
            <span><i className="legend-dot approved" />Admitted path</span>
            <span><i className="legend-dot restricted" />Restricted</span>
            <span><i className="legend-dot human" />Human</span>
          </div>
        </section>

        <aside className="story-panel">
          <section className="inspector-section primary-decision">
            <div className="section-heading">
              <ShieldCheck size={18} />
              <h2>What To Watch</h2>
            </div>
            <p className="decision-copy">
              The model proposes tool-head waypoints. The policy prover checks every segment before anything moves.
            </p>
            <div className="watch-steps">
              <span>1 Agent proposes</span>
              <span>2 Prover checks</span>
              <span>3 Agent adapts</span>
              <span>4 Robot executes</span>
            </div>
          </section>

          <section className="inspector-section terminal-section story-section">
            <div className="section-heading">
              <Radar size={18} />
              <h2>Experiment</h2>
            </div>
            <div className="story-list">
              {storyEvents.map((event) => (
                <button
                  key={event.id}
                  type="button"
                  className={`story-row tone-${storyTone(event)}`}
                  onClick={() => setStep((previous) => normalizeStep(event, previous))}
                >
                  <strong>{storyLabel(event)}</strong>
                  <span>{compactSummary(event)}</span>
                </button>
              ))}
              {!storyEvents.length && (
                <p className="terminal-empty">
                  Click Run Experiment. The first path may cross the red zone; if it does, the prover returns a counterexample and the agent gets one replan.
                </p>
              )}
            </div>
          </section>

          <section className="details-panel">
            <details>
              <summary>
                <ChevronDown size={16} />
                Policies checked
              </summary>
              <ul>
                <li>No waypoint may leave the workspace.</li>
                <li>No path segment may enter the red restricted volume.</li>
                <li>If a human is nearby, speed is clamped and pause obligations apply.</li>
                <li>Every executed motion produces an audit event.</li>
              </ul>
            </details>
            <details>
              <summary>
                <ChevronDown size={16} />
                POC scope
              </summary>
              <p>
                This version governs the tool-head path, not full robot-body collision. That keeps the visual invariant exactly aligned with what the prover checks.
              </p>
            </details>
            <details>
              <summary>
                <ChevronDown size={16} />
                Latest decision packet
              </summary>
              <pre>{JSON.stringify(sanitizeDecision(latestDecisionEvent?.decision), null, 2)}</pre>
            </details>
            <details>
              <summary>
                <ChevronDown size={16} />
                Latest action envelope
              </summary>
              <pre>{JSON.stringify(step.action ?? null, null, 2)}</pre>
            </details>
            <details>
              <summary>
                <ChevronDown size={16} />
                OpenShell transition point
              </summary>
              <p>
                In OpenShell, the agent could run inside the runtime and submit this same action envelope to a policy-prover service before any tool, delegation, or actuator command executes.
              </p>
            </details>
          </section>

          <label className="speed-control simple-speed">
            <Gauge size={16} />
            <span>Sim speed</span>
            <input
              aria-label="Simulation speed"
              type="range"
              min="0.5"
              max="2"
              step="0.25"
              value={playbackSpeed}
              onChange={(event) => setPlaybackSpeed(Number(event.target.value))}
            />
            <strong>{playbackSpeed.toFixed(2)}x</strong>
          </label>
        </aside>
      </section>
    </main>
  );
}

export default App;
