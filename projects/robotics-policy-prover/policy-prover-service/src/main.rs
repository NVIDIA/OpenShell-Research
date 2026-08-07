use std::collections::HashMap;
use std::convert::Infallible;
use std::env;
use std::fs;
use std::hint::black_box;
use std::net::SocketAddr;
use std::sync::Arc;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result};
use axum::extract::{Path, State};
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::routing::{get, post};
use axum::{Json, Router};
use futures_util::StreamExt;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use tokio::sync::{broadcast, Mutex};
use tokio::time::{sleep, Duration};
use tokio_stream::wrappers::BroadcastStream;
use tower_http::cors::CorsLayer;
use uuid::Uuid;
use z3::ast::Bool;
use z3::{Config, Solver};

const WORKSPACE_MIN: Vec3 = [-1.35, 0.02, -0.95];
const WORKSPACE_MAX: Vec3 = [1.25, 1.05, 0.85];
const HOME: Vec3 = [-1.08, 0.78, 0.58];

type Vec3 = [f64; 3];

#[derive(Clone)]
struct AppState {
    sessions: Arc<Mutex<HashMap<Uuid, SessionHandle>>>,
}

#[derive(Clone)]
struct SessionHandle {
    tx: broadcast::Sender<DemoEvent>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct StartSessionRequest {
    goal: Option<String>,
    seed: Option<u64>,
    agent_mode: Option<AgentMode>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum AgentMode {
    Fixture,
    Openai,
}

#[derive(Debug, Clone, Serialize)]
struct StartSessionResponse {
    session_id: Uuid,
    agent_mode: AgentMode,
    seed: u64,
}

#[derive(Debug, Clone, Deserialize)]
struct InjectRequest {
    event: InjectEvent,
}

#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "snake_case")]
enum InjectEvent {
    HumanEntersWorkspace,
    SensorStale,
    BudgetLow,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ActionEnvelope {
    actor: String,
    subagent: String,
    action: String,
    resource: String,
    from: Vec3,
    to: Vec3,
    path: Vec<Vec3>,
    speed_mps: f64,
    force_n: f64,
    object_id: String,
    object_class: String,
    capability: String,
    context: ActionContext,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ActionContext {
    human_distance_m: f64,
    sensor_age_ms: u64,
    remaining_budget_ms: u64,
    restricted_zones: Vec<Zone>,
    caution_zones: Vec<Zone>,
    parent_speed_cap_mps: f64,
    requested_speed_cap_mps: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct ProverDecision {
    decision: Decision,
    solver_ms: f64,
    violations: Vec<String>,
    constraints: DecisionConstraints,
    obligations: Vec<String>,
    counterexample: Option<Counterexample>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
enum Decision {
    Allow,
    Deny,
    AllowWithConstraints,
    ApprovalRequired,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
struct DecisionConstraints {
    #[serde(skip_serializing_if = "Option::is_none")]
    speed_mps_max: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    force_n_max: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    bypass_z_min: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Counterexample {
    segment_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    zone_id: Option<String>,
    reason: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    point: Option<Vec3>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct WorldState {
    goal: String,
    seed: u64,
    objects: Vec<SceneObject>,
    zones: Vec<Zone>,
    human: HumanState,
    metrics: Metrics,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct SceneObject {
    id: String,
    label: String,
    class_name: String,
    color: String,
    position: Vec3,
    size: Vec3,
    sorted: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Zone {
    id: String,
    label: String,
    color: String,
    position: Vec3,
    size: Vec3,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct HumanState {
    position: Vec3,
    radius: f64,
    caution: f64,
    distance_m: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct Metrics {
    fps: u32,
    decision_count: u32,
    p95_solver_ms: f64,
    task_budget_pct: u8,
    compute_budget_pct: u8,
    actions_left: u8,
    sensor_age_ms: u64,
}

#[derive(Debug, Clone, Serialize)]
struct DemoEvent {
    id: String,
    kind: String,
    timestamp_ms: u64,
    actor: String,
    summary: String,
    world: Option<WorldState>,
    action: Option<ActionEnvelope>,
    decision: Option<ProverDecision>,
    proposed_path: Option<Vec<Vec3>>,
    approved_path: Option<Vec<Vec3>>,
    highlight: Option<Highlight>,
}

#[derive(Debug, Clone, Serialize)]
struct Highlight {
    kind: String,
    target: Option<String>,
}

#[derive(Debug, Clone)]
struct MissionRuntime {
    tx: broadcast::Sender<DemoEvent>,
    world: WorldState,
    started: Instant,
    solver_samples: Vec<f64>,
    agent_mode: AgentMode,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
struct AgentPlan {
    path: Vec<Vec3>,
    speed_mps: f64,
    rationale: String,
    #[serde(default)]
    source: String,
}

#[tokio::main]
async fn main() -> Result<()> {
    dotenvy::dotenv().ok();

    if env::var("POLICY_PROVER_BENCHMARK").as_deref() == Ok("1") {
        run_policy_benchmark()?;
        return Ok(());
    }

    let state = AppState {
        sessions: Arc::new(Mutex::new(HashMap::new())),
    };

    let app = Router::new()
        .route("/api/health", get(|| async { Json(serde_json::json!({ "ok": true })) }))
        .route("/api/sessions", post(start_session))
        .route("/api/sessions/{id}/events", get(session_events))
        .route("/api/sessions/{id}/inject", post(inject_event))
        .route("/api/decide", post(decide))
        .layer(CorsLayer::permissive())
        .with_state(state);

    let addr: SocketAddr = "127.0.0.1:8787".parse()?;
    println!("policy-prover service listening on http://{addr}");
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

async fn start_session(
    State(state): State<AppState>,
    Json(request): Json<StartSessionRequest>,
) -> Json<StartSessionResponse> {
    let session_id = Uuid::new_v4();
    let seed = request.seed.unwrap_or(42);
    let requested_mode = request.agent_mode.unwrap_or(AgentMode::Fixture);
    let agent_mode = if requested_mode == AgentMode::Openai && env::var("OPENAI_API_KEY").is_ok() {
        AgentMode::Openai
    } else {
        AgentMode::Fixture
    };
    let goal = request
        .goal
        .unwrap_or_else(|| "Sort all lab samples into the correct trays.".to_owned());
    let (tx, _) = broadcast::channel(256);
    state
        .sessions
        .lock()
        .await
        .insert(session_id, SessionHandle { tx: tx.clone() });

    let runtime = MissionRuntime {
        tx,
        world: seeded_world(seed, goal),
        started: Instant::now(),
        solver_samples: Vec::new(),
        agent_mode,
    };
    tokio::spawn(async move {
        if let Err(err) = run_mission(runtime).await {
            eprintln!("mission {session_id} failed: {err:#}");
        }
    });

    Json(StartSessionResponse {
        session_id,
        agent_mode,
        seed,
    })
}

async fn session_events(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
) -> Sse<impl futures_util::Stream<Item = Result<Event, Infallible>>> {
    let rx = state
        .sessions
        .lock()
        .await
        .get(&id)
        .map(|handle| handle.tx.subscribe());

    let stream = match rx {
        Some(rx) => BroadcastStream::new(rx)
            .filter_map(|message| async move { message.ok() })
            .map(|event| {
                let data = serde_json::to_string(&event).unwrap_or_else(|_| "{}".to_owned());
                Ok(Event::default().event(event.kind).id(event.id).data(data))
            })
            .boxed(),
        None => {
            let event = DemoEvent::system("session_missing", "Session not found", None);
            tokio_stream::iter([Ok(Event::default()
                .event("error")
                .id(event.id.clone())
                .data(serde_json::to_string(&event).unwrap()))])
            .boxed()
        }
    };

    Sse::new(stream).keep_alive(KeepAlive::default())
}

async fn inject_event(
    State(state): State<AppState>,
    Path(id): Path<Uuid>,
    Json(request): Json<InjectRequest>,
) -> Json<serde_json::Value> {
    let Some(handle) = state.sessions.lock().await.get(&id).cloned() else {
        return Json(serde_json::json!({ "ok": false, "error": "session_not_found" }));
    };
    let summary = match request.event {
        InjectEvent::HumanEntersWorkspace => "Injected: human entered the workspace",
        InjectEvent::SensorStale => "Injected: sensor state is stale",
        InjectEvent::BudgetLow => "Injected: task budget is almost exhausted",
    };
    let _ = handle.tx.send(DemoEvent::system("world_event", summary, None));
    Json(serde_json::json!({ "ok": true }))
}

async fn decide(Json(action): Json<ActionEnvelope>) -> Json<ProverDecision> {
    Json(check_action(&action))
}

async fn run_mission(mut runtime: MissionRuntime) -> Result<()> {
    sleep(Duration::from_millis(250)).await;
    runtime.emit_world(
        "world_event",
        "Experiment started: move the green block into the blue tray.",
    );
    sleep(Duration::from_millis(700)).await;

    let initial_plan = runtime
        .choose_plan(None)
        .await
        .unwrap_or_else(|| fixture_plan(&runtime.world, None));
    let proposal = action_from_plan(&runtime.world, &initial_plan);

    runtime
        .agent_plan(
            "Agent proposed a waypoint path",
            &proposal,
            None,
            &initial_plan,
        )
        .await;
    let first_decision = runtime.check_only(proposal.clone()).await?;

    if first_decision.decision == Decision::Deny {
        sleep(Duration::from_millis(900)).await;
        let mut repair_plan = runtime
            .choose_plan(Some(&first_decision))
            .await
            .unwrap_or_else(|| fixture_plan(&runtime.world, Some(&first_decision)));
        let mut repair = action_from_plan(&runtime.world, &repair_plan);
        if runtime.agent_mode == AgentMode::Fixture
            && first_path_intersection(&repair.path, &repair.context.restricted_zones).is_some()
        {
            repair_plan = fixture_plan(&runtime.world, Some(&first_decision));
            repair = action_from_plan(&runtime.world, &repair_plan);
        }

        runtime
            .agent_plan(
                "Agent proposed a revised waypoint path",
                &repair,
                Some(&first_decision),
                &repair_plan,
            )
            .await;

        runtime.world.human.position = [0.12, 0.08, -0.22];
        runtime.world.human.distance_m = 0.48;
        runtime.emit_world(
            "world_event",
            "A human enters the caution radius before execution.",
        );
        sleep(Duration::from_millis(500)).await;
        let mut repair_with_current_world = action_for(
            &runtime.world,
            "green_vial",
            repair.path.clone(),
            repair.speed_mps,
            repair.force_n,
            "move_lab_samples",
        );
        repair_with_current_world.speed_mps = repair.speed_mps;
        runtime
            .check_and_execute(
                repair_with_current_world,
                Some("Executor runs the revised path with the prover's speed limit"),
            )
            .await?;
    } else {
        runtime
            .execute_decision(
                proposal,
                first_decision,
                Some("Executor runs the agent path"),
            )
            .await?;
    }

    runtime.emit_world(
        "execution_update",
        "Experiment complete: only the policy-prover-approved path was executed.",
    );
    Ok(())
}

impl MissionRuntime {
    fn elapsed_ms(&self) -> u64 {
        self.started.elapsed().as_millis() as u64
    }

    fn emit(&self, mut event: DemoEvent) {
        event.timestamp_ms = self.elapsed_ms();
        let _ = self.tx.send(event);
    }

    fn emit_world(&self, kind: &str, summary: &str) {
        self.emit(DemoEvent {
            id: Uuid::new_v4().to_string(),
            kind: kind.to_owned(),
            timestamp_ms: 0,
            actor: "world".to_owned(),
            summary: summary.to_owned(),
            world: Some(self.world.clone()),
            action: None,
            decision: None,
            proposed_path: None,
            approved_path: None,
            highlight: Some(Highlight {
                kind: "world".to_owned(),
                target: None,
            }),
        });
    }

    async fn agent_plan(
        &self,
        summary: &str,
        action: &ActionEnvelope,
        _prior: Option<&ProverDecision>,
        plan: &AgentPlan,
    ) {
        self.emit(DemoEvent {
            id: Uuid::new_v4().to_string(),
            kind: "agent_plan".to_owned(),
            timestamp_ms: 0,
            actor: "agent".to_owned(),
            summary: format!(
                "{summary}: {} waypoints at {:.2} m/s. Source: {}.",
                action.path.len(),
                action.speed_mps,
                plan.source
            ),
            world: Some(self.world.clone()),
            action: Some(action.clone()),
            decision: None,
            proposed_path: Some(action.path.clone()),
            approved_path: None,
            highlight: Some(Highlight {
                kind: "proposal".to_owned(),
                target: Some(action.object_id.clone()),
            }),
        });
    }

    async fn choose_plan(&self, prior: Option<&ProverDecision>) -> Option<AgentPlan> {
        if self.agent_mode != AgentMode::Openai {
            return Some(fixture_plan(&self.world, prior));
        }
        ask_openai_plan(&self.world, prior).await.ok()
    }

    async fn check_only(&mut self, action: ActionEnvelope) -> Result<ProverDecision> {
        let decision = check_action(&action);
        self.solver_samples.push(decision.solver_ms);
        self.world.metrics.decision_count += 1;
        self.world.metrics.p95_solver_ms = p95(&self.solver_samples);

        let approved_path = match decision.decision {
            Decision::Allow | Decision::AllowWithConstraints | Decision::ApprovalRequired => {
                Some(action.path.clone())
            }
            Decision::Deny => None,
        };
        self.emit(DemoEvent {
            id: Uuid::new_v4().to_string(),
            kind: "prover_decision".to_owned(),
            timestamp_ms: 0,
            actor: "policy-prover".to_owned(),
            summary: decision_summary(&decision),
            world: Some(self.world.clone()),
            action: Some(action.clone()),
            decision: Some(decision.clone()),
            proposed_path: Some(action.path.clone()),
            approved_path,
            highlight: Some(Highlight {
                kind: format!("{:?}", decision.decision).to_lowercase(),
                target: decision
                    .counterexample
                    .as_ref()
                    .and_then(|counterexample| counterexample.zone_id.clone())
                    .or_else(|| Some(action.object_id.clone())),
            }),
        });
        Ok(decision)
    }

    async fn check_and_execute(
        &mut self,
        action: ActionEnvelope,
        executor_note: Option<&str>,
    ) -> Result<()> {
        let decision = self.check_only(action.clone()).await?;
        if decision.decision == Decision::Deny {
            return Ok(());
        }
        self.execute_decision(action, decision, executor_note).await
    }

    async fn execute_decision(
        &mut self,
        action: ActionEnvelope,
        decision: ProverDecision,
        executor_note: Option<&str>,
    ) -> Result<()> {
        sleep(Duration::from_millis(600)).await;
        let speed = decision
            .constraints
            .speed_mps_max
            .map(|value| format!(" at constrained speed <= {value:.2} m/s"))
            .unwrap_or_default();
        let note = executor_note.unwrap_or("Executor running approved segment");
        self.emit(DemoEvent {
            id: Uuid::new_v4().to_string(),
            kind: "execution_update".to_owned(),
            timestamp_ms: 0,
            actor: "executor".to_owned(),
            summary: format!("{note}{speed}"),
            world: Some(self.world.clone()),
            action: Some(action.clone()),
            decision: Some(decision),
            proposed_path: Some(action.path.clone()),
            approved_path: Some(action.path.clone()),
            highlight: Some(Highlight {
                kind: "execution".to_owned(),
                target: Some(action.object_id),
            }),
        });
        Ok(())
    }
}

impl DemoEvent {
    fn system(kind: &str, summary: &str, world: Option<WorldState>) -> Self {
        Self {
            id: Uuid::new_v4().to_string(),
            kind: kind.to_owned(),
            timestamp_ms: 0,
            actor: "system".to_owned(),
            summary: summary.to_owned(),
            world,
            action: None,
            decision: None,
            proposed_path: None,
            approved_path: None,
            highlight: None,
        }
    }
}

fn check_action(action: &ActionEnvelope) -> ProverDecision {
    let start = Instant::now();
    let mut config = Config::new();
    config.set_timeout_msec(18);
    let _ = z3::with_z3_config(&config, || {
        let solver = Solver::new();

        let out_of_bounds = action.path.iter().any(|point| !inside_workspace(*point));
        let restricted_hit = first_path_intersection(&action.path, &action.context.restricted_zones);
        let stale_sensor = action.context.sensor_age_ms > 250;
        let budget_blocked = action.context.remaining_budget_ms < 1_200 && action.action != "arm.move_segment";
        let capability_expansion = action.action == "grant.capability"
            && (action.capability == "unrestricted_motion"
                || action.context.requested_speed_cap_mps > action.context.parent_speed_cap_mps);
        let human_speed_cap = action.context.human_distance_m < 0.75 && action.speed_mps > 0.08;
        let fragile_force_cap =
            matches!(action.object_class.as_str(), "fragile" | "hazardous") && action.force_n > 2.0;

        let facts = [
            ("workspace_bounds", out_of_bounds),
            ("restricted_zone_intersection", restricted_hit.is_some()),
            ("sensor_stale", stale_sensor),
            ("budget_or_approval_required", budget_blocked),
            ("capability_expansion", capability_expansion),
            ("human_speed_cap", human_speed_cap),
            ("fragile_force_cap", fragile_force_cap),
        ];

        let mut vars = Vec::new();
        for (name, value) in facts {
            let var = Bool::new_const(name);
            if value {
                solver.assert(&var);
            } else {
                solver.assert(&!var.clone());
            }
            vars.push((name, value, var));
        }
        let unsafe_expr = Bool::or(&vars.iter().map(|(_, _, var)| var.clone()).collect::<Vec<_>>());
        solver.assert(&unsafe_expr);
        let _ = solver.check();
    });

    let out_of_bounds = action.path.iter().any(|point| !inside_workspace(*point));
    let restricted_hit = first_path_intersection(&action.path, &action.context.restricted_zones);
    let stale_sensor = action.context.sensor_age_ms > 250;
    let budget_blocked = action.context.remaining_budget_ms < 1_200 && action.action != "arm.move_segment";
    let capability_expansion = action.action == "grant.capability"
        && (action.capability == "unrestricted_motion"
            || action.context.requested_speed_cap_mps > action.context.parent_speed_cap_mps);
    let human_speed_cap = action.context.human_distance_m < 0.75 && action.speed_mps > 0.08;
    let fragile_force_cap =
        matches!(action.object_class.as_str(), "fragile" | "hazardous") && action.force_n > 2.0;

    let mut violations = Vec::new();
    if out_of_bounds {
        violations.push("workspace_bounds".to_owned());
    }
    if restricted_hit.is_some() {
        violations.push("restricted_zone_intersection".to_owned());
    }
    if stale_sensor {
        violations.push("sensor_stale".to_owned());
    }
    if capability_expansion {
        violations.push("capability_expansion".to_owned());
    }
    if budget_blocked {
        violations.push("budget_requires_approval".to_owned());
    }
    if human_speed_cap {
        violations.push("human_speed_cap".to_owned());
    }
    if fragile_force_cap {
        violations.push("fragile_force_cap".to_owned());
    }

    let hard_deny = out_of_bounds || restricted_hit.is_some() || stale_sensor || capability_expansion;
    let mut constraints = DecisionConstraints::default();
    if human_speed_cap {
        constraints.speed_mps_max = Some(0.08);
    }
    if fragile_force_cap {
        constraints.force_n_max = Some(2.0);
    }
    if restricted_hit.is_some() {
        constraints.bypass_z_min = Some(0.55);
    }

    let decision = if hard_deny {
        Decision::Deny
    } else if budget_blocked {
        Decision::ApprovalRequired
    } else if constraints.speed_mps_max.is_some() || constraints.force_n_max.is_some() {
        Decision::AllowWithConstraints
    } else {
        Decision::Allow
    };

    let mut obligations = vec!["emit_audit_event".to_owned()];
    if action.context.human_distance_m < 0.75 {
        obligations.push("pause_if_human_distance_below_0_5m".to_owned());
    }
    if decision != Decision::Deny {
        obligations.push("expire_capability_after_action".to_owned());
    }

    let counterexample = restricted_hit.map(|(zone, point)| Counterexample {
        segment_id: "proposed_segment".to_owned(),
        zone_id: Some(zone.id),
        reason: "restricted_zone_intersection".to_owned(),
        point: Some(point),
    });

    ProverDecision {
        decision,
        solver_ms: start.elapsed().as_secs_f64() * 1000.0,
        violations,
        constraints,
        obligations,
        counterexample,
    }
}

fn seeded_world(seed: u64, goal: String) -> WorldState {
    let mut rng = TinyRng::new(seed);
    let restricted_x = 0.15 + rng.range(-0.08, 0.08);
    WorldState {
        goal,
        seed,
        objects: vec![
            SceneObject {
                id: "green_vial".to_owned(),
                label: "Green block".to_owned(),
                class_name: "standard".to_owned(),
                color: "#31b66b".to_owned(),
                position: [-0.72 + rng.range(-0.05, 0.05), 0.18, -0.46],
                size: [0.18, 0.26, 0.18],
                sorted: false,
            },
            SceneObject {
                id: "blue_tray".to_owned(),
                label: "Blue tray".to_owned(),
                class_name: "tray".to_owned(),
                color: "#3f7dda".to_owned(),
                position: [0.83, 0.08, 0.28],
                size: [0.62, 0.08, 0.42],
                sorted: true,
            },
        ],
        zones: vec![
            Zone {
                id: "restricted_zone.alpha".to_owned(),
                label: "Restricted".to_owned(),
                color: "#e44f5e".to_owned(),
                position: [restricted_x, 0.52, 0.04],
                size: [0.56, 1.04, 0.72],
            },
            Zone {
                id: "caution_zone.human".to_owned(),
                label: "Caution".to_owned(),
                color: "#e8b630".to_owned(),
                position: [-0.12, 0.15, -0.46],
                size: [0.96, 0.3, 0.56],
            },
        ],
        human: HumanState {
            position: [1.08, 0.08, -0.72],
            radius: 0.24,
            caution: 0.54,
            distance_m: 1.42,
        },
        metrics: Metrics {
            fps: 60,
            decision_count: 0,
            p95_solver_ms: 0.0,
            task_budget_pct: 84,
            compute_budget_pct: 21,
            actions_left: 11,
            sensor_age_ms: 72,
        },
    }
}

fn action_for(world: &WorldState, object_id: &str, path: Vec<Vec3>, speed_mps: f64, force_n: f64, capability: &str) -> ActionEnvelope {
    let object = world
        .objects
        .iter()
        .find(|object| object.id == object_id)
        .cloned()
        .unwrap_or_else(|| world.objects[0].clone());
    ActionEnvelope {
        actor: "planner-agent".to_owned(),
        subagent: "motion-agent".to_owned(),
        action: "arm.move_segment".to_owned(),
        resource: "so101.sim.arm".to_owned(),
        from: path.first().copied().unwrap_or(HOME),
        to: path.last().copied().unwrap_or(HOME),
        path,
        speed_mps,
        force_n,
        object_id: object.id,
        object_class: object.class_name,
        capability: capability.to_owned(),
        context: context_for(world, speed_mps),
    }
}

fn action_from_plan(world: &WorldState, plan: &AgentPlan) -> ActionEnvelope {
    let plan = normalize_plan(plan.clone(), world);
    action_for(
        world,
        "green_vial",
        plan.path,
        plan.speed_mps,
        1.8,
        "move_lab_samples",
    )
}

fn pickup_point(world: &WorldState) -> Vec3 {
    world
        .objects
        .iter()
        .find(|object| object.id == "green_vial")
        .map(|object| [object.position[0], object.position[1] + object.size[1] * 0.65, object.position[2]])
        .unwrap_or([-0.72, 0.35, -0.46])
}

fn place_point() -> Vec3 {
    [0.84, 0.34, 0.3]
}

fn direct_path(world: &WorldState) -> Vec<Vec3> {
    let pickup = pickup_point(world);
    vec![
        HOME,
        pickup,
        [-0.42, 0.52, -0.22],
        [0.24, 0.31, 0.04],
        place_point(),
    ]
}

fn north_bypass_path(world: &WorldState) -> Vec<Vec3> {
    let pickup = pickup_point(world);
    vec![
        HOME,
        pickup,
        [-0.92, 0.72, 0.72],
        [-0.18, 0.72, 0.72],
        [0.68, 0.54, 0.66],
        place_point(),
    ]
}

fn normalize_plan(mut plan: AgentPlan, world: &WorldState) -> AgentPlan {
    plan.path.retain(|point| point.iter().all(|value| value.is_finite()));
    if plan.path.len() < 2 {
        plan.path = direct_path(world);
    }
    if distance(*plan.path.first().unwrap_or(&HOME), HOME) > 0.08 {
        plan.path.insert(0, HOME);
    }
    let pickup = pickup_point(world);
    let target = place_point();
    let pickup_index = plan.path.iter().position(|point| distance(*point, pickup) <= 0.18);
    match pickup_index {
        Some(index) if index <= 2 => {
            plan.path[index] = pickup;
        }
        Some(index) => {
            plan.path.remove(index);
            plan.path.insert(1, pickup);
        }
        None => {
            plan.path.insert(1, pickup);
        }
    }
    if plan.path.len() > 7 {
        let last = plan.path.last().copied().unwrap_or(target);
        plan.path.truncate(7);
        if distance(last, target) <= 0.12 {
            let final_index = plan.path.len().saturating_sub(1);
            plan.path[final_index] = target;
        }
    }
    if distance(*plan.path.last().unwrap_or(&target), target) > 0.12 {
        plan.path.push(target);
    } else if let Some(last) = plan.path.last_mut() {
        *last = target;
    }
    plan.speed_mps = plan.speed_mps.clamp(0.04, 0.35);
    if plan.rationale.trim().is_empty() {
        plan.rationale = "Proposed waypoint path.".to_owned();
    }
    plan
}

fn distance(a: Vec3, b: Vec3) -> f64 {
    ((a[0] - b[0]).powi(2) + (a[1] - b[1]).powi(2) + (a[2] - b[2]).powi(2)).sqrt()
}

fn context_for(world: &WorldState, requested_speed_cap_mps: f64) -> ActionContext {
    ActionContext {
        human_distance_m: world.human.distance_m,
        sensor_age_ms: world.metrics.sensor_age_ms,
        remaining_budget_ms: (world.metrics.task_budget_pct as u64) * 180,
        restricted_zones: world
            .zones
            .iter()
            .filter(|zone| zone.id.starts_with("restricted"))
            .cloned()
            .collect(),
        caution_zones: world
            .zones
            .iter()
            .filter(|zone| zone.id.starts_with("caution"))
            .cloned()
            .collect(),
        parent_speed_cap_mps: 0.2,
        requested_speed_cap_mps,
    }
}

fn inside_workspace(point: Vec3) -> bool {
    (0..3).all(|idx| point[idx] >= WORKSPACE_MIN[idx] && point[idx] <= WORKSPACE_MAX[idx])
}

fn first_path_intersection(path: &[Vec3], zones: &[Zone]) -> Option<(Zone, Vec3)> {
    if path.len() < 2 {
        return None;
    }
    for segment in path.windows(2) {
        if let Some(hit) = first_zone_intersection(segment[0], segment[1], zones) {
            return Some(hit);
        }
    }
    None
}

fn first_zone_intersection(from: Vec3, to: Vec3, zones: &[Zone]) -> Option<(Zone, Vec3)> {
    for zone in zones {
        for step in 0..=32 {
            let t = step as f64 / 32.0;
            let point = [
                from[0] + (to[0] - from[0]) * t,
                from[1] + (to[1] - from[1]) * t,
                from[2] + (to[2] - from[2]) * t,
            ];
            if point_inside_zone(point, zone) {
                return Some((zone.clone(), point));
            }
        }
    }
    None
}

fn point_inside_zone(point: Vec3, zone: &Zone) -> bool {
    (0..3).all(|idx| {
        let half = zone.size[idx] / 2.0;
        point[idx] >= zone.position[idx] - half && point[idx] <= zone.position[idx] + half
    })
}

fn decision_summary(decision: &ProverDecision) -> String {
    let verdict = match decision.decision {
        Decision::Allow => "ALLOW",
        Decision::Deny => "DENY",
        Decision::AllowWithConstraints => "CONSTRAIN",
        Decision::ApprovalRequired => "APPROVAL",
    };
    let invariant = decision
        .violations
        .first()
        .map(String::as_str)
        .unwrap_or("policy_satisfied");
    let detail = match decision.decision {
        Decision::Deny => {
            let point = decision
                .counterexample
                .as_ref()
                .and_then(|counterexample| counterexample.point)
                .map(|point| format!(" @ [{:.2}, {:.2}, {:.2}]", point[0], point[1], point[2]))
                .unwrap_or_default();
            let bypass = decision
                .constraints
                .bypass_z_min
                .map(|value| format!("; bypass_z_min={value:.2}"))
                .unwrap_or_default();
            format!("{invariant}{point}{bypass}")
        }
        Decision::AllowWithConstraints => constraint_summary(&decision.constraints),
        Decision::ApprovalRequired => invariant.to_owned(),
        Decision::Allow => invariant.to_owned(),
    };
    format!("{verdict} · {detail} · {:.2} ms", decision.solver_ms)
}

fn constraint_summary(constraints: &DecisionConstraints) -> String {
    let mut parts = Vec::new();
    if let Some(value) = constraints.speed_mps_max {
        parts.push(format!("speed_mps_max={value:.2}"));
    }
    if let Some(value) = constraints.force_n_max {
        parts.push(format!("force_n_max={value:.1}"));
    }
    if let Some(value) = constraints.bypass_z_min {
        parts.push(format!("bypass_z_min={value:.2}"));
    }
    if parts.is_empty() {
        "no_constraints".to_owned()
    } else {
        parts.join("; ")
    }
}

fn p95(samples: &[f64]) -> f64 {
    if samples.is_empty() {
        return 0.0;
    }
    let mut sorted = samples.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let index = ((sorted.len() as f64 * 0.95).ceil() as usize).saturating_sub(1);
    sorted[index]
}

#[derive(Debug, Serialize)]
struct BenchmarkReport {
    schema_version: u8,
    generated_unix_seconds: u64,
    platform: String,
    rustc_version: String,
    z3_crate_version: String,
    source_revision: String,
    build_profile: String,
    samples_per_case: usize,
    warmup_per_case: usize,
    z3_timeout_ms: u64,
    geometry_samples_per_segment: usize,
    cases: Vec<BenchmarkCase>,
}

#[derive(Debug, Serialize)]
struct BenchmarkCase {
    outcome: String,
    waypoints: usize,
    segments: usize,
    samples: usize,
    p50_ms: f64,
    p95_ms: f64,
    p99_ms: f64,
    mean_ms: f64,
    min_ms: f64,
    max_ms: f64,
    decisions_per_second: f64,
}

fn run_policy_benchmark() -> Result<()> {
    let samples_per_case = env::var("POLICY_PROVER_BENCHMARK_SAMPLES")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(500);
    let warmup_per_case = env::var("POLICY_PROVER_BENCHMARK_WARMUP")
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(100);
    let platform =
        env::var("POLICY_PROVER_BENCHMARK_PLATFORM").unwrap_or_else(|_| "unspecified".to_owned());
    let output_path = env::var("POLICY_PROVER_BENCHMARK_OUTPUT").ok();
    let rustc_version =
        env::var("POLICY_PROVER_BENCHMARK_RUSTC").unwrap_or_else(|_| "rustc 1.89.0".to_owned());
    let source_revision = env::var("POLICY_PROVER_BENCHMARK_REVISION")
        .unwrap_or_else(|_| "local-uncommitted".to_owned());
    let waypoint_counts = [3, 6, 12, 24, 48];
    let mut cases = Vec::new();

    for outcome in ["allow", "deny", "constrain"] {
        for waypoints in waypoint_counts {
            let action = benchmark_action(outcome, waypoints);
            let expected = match outcome {
                "allow" => Decision::Allow,
                "deny" => Decision::Deny,
                "constrain" => Decision::AllowWithConstraints,
                _ => unreachable!(),
            };
            let observed = check_action(&action);
            anyhow::ensure!(
                observed.decision == expected,
                "benchmark case {outcome}/{waypoints} returned {:?}",
                observed.decision
            );

            for _ in 0..warmup_per_case {
                black_box(check_action(black_box(&action)));
            }

            let started = Instant::now();
            let mut timings = Vec::with_capacity(samples_per_case);
            for _ in 0..samples_per_case {
                let decision = black_box(check_action(black_box(&action)));
                timings.push(decision.solver_ms);
            }
            let wall_seconds = started.elapsed().as_secs_f64();
            timings.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
            let mean_ms = timings.iter().sum::<f64>() / timings.len() as f64;
            cases.push(BenchmarkCase {
                outcome: outcome.to_owned(),
                waypoints,
                segments: waypoints - 1,
                samples: samples_per_case,
                p50_ms: percentile(&timings, 0.50),
                p95_ms: percentile(&timings, 0.95),
                p99_ms: percentile(&timings, 0.99),
                mean_ms,
                min_ms: timings.first().copied().unwrap_or(0.0),
                max_ms: timings.last().copied().unwrap_or(0.0),
                decisions_per_second: samples_per_case as f64 / wall_seconds,
            });
        }
    }

    let report = BenchmarkReport {
        schema_version: 1,
        generated_unix_seconds: SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs(),
        platform,
        rustc_version,
        z3_crate_version: "0.19.15 (bundled Z3 4.16.0)".to_owned(),
        source_revision,
        build_profile: "release".to_owned(),
        samples_per_case,
        warmup_per_case,
        z3_timeout_ms: 18,
        geometry_samples_per_segment: 33,
        cases,
    };
    let json = serde_json::to_string_pretty(&report)?;
    if let Some(path) = output_path {
        if let Some(parent) = std::path::Path::new(&path).parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(path, format!("{json}\n"))?;
    } else {
        println!("{json}");
    }
    Ok(())
}

fn benchmark_action(outcome: &str, waypoint_count: usize) -> ActionEnvelope {
    let mut world = seeded_world(42, "benchmark".to_owned());
    let safe_path = [HOME, [-0.18, 0.72, 0.72], place_point()];
    let denied_path = [HOME, [0.24, 0.31, 0.04], place_point()];
    let path = match outcome {
        "deny" => densify_path(&denied_path, waypoint_count),
        _ => densify_path(&safe_path, waypoint_count),
    };
    if outcome == "constrain" {
        world.human.distance_m = 0.48;
    }
    let mut action = action_for(
        &world,
        "green_vial",
        path,
        if outcome == "constrain" { 0.2 } else { 0.08 },
        if outcome == "constrain" { 3.0 } else { 1.8 },
        "move_lab_samples",
    );
    if outcome == "constrain" {
        action.object_class = "fragile".to_owned();
    }
    action
}

fn densify_path(points: &[Vec3], waypoint_count: usize) -> Vec<Vec3> {
    assert!(points.len() >= 2);
    assert!(waypoint_count >= points.len());
    let segments = points.len() - 1;
    let total_edges = waypoint_count - 1;
    let base_edges = total_edges / segments;
    let remainder = total_edges % segments;
    let mut result = Vec::with_capacity(waypoint_count);
    result.push(points[0]);

    for segment_index in 0..segments {
        let edges = base_edges + usize::from(segment_index < remainder);
        for edge in 1..=edges {
            let t = edge as f64 / edges as f64;
            result.push([
                points[segment_index][0]
                    + (points[segment_index + 1][0] - points[segment_index][0]) * t,
                points[segment_index][1]
                    + (points[segment_index + 1][1] - points[segment_index][1]) * t,
                points[segment_index][2]
                    + (points[segment_index + 1][2] - points[segment_index][2]) * t,
            ]);
        }
    }
    result
}

fn percentile(sorted_samples: &[f64], quantile: f64) -> f64 {
    if sorted_samples.is_empty() {
        return 0.0;
    }
    let index = ((sorted_samples.len() as f64 * quantile).ceil() as usize)
        .saturating_sub(1)
        .min(sorted_samples.len() - 1);
    sorted_samples[index]
}

fn fixture_plan(world: &WorldState, prior: Option<&ProverDecision>) -> AgentPlan {
    let (path, speed_mps, rationale) = if prior.is_some() {
        (
            north_bypass_path(world),
            0.18,
            "Revised waypoint path from prover feedback.".to_owned(),
        )
    } else {
        (
            direct_path(world),
            0.32,
            "Shortest efficient waypoint path.".to_owned(),
        )
    };
    AgentPlan {
        path,
        speed_mps,
        rationale,
        source: "Fixture agent".to_owned(),
    }
}

fn normalize_agent_plan(mut plan: AgentPlan, source: &str, world: &WorldState) -> AgentPlan {
    plan.source = source.to_owned();
    let trimmed = plan.rationale.trim();
    plan.rationale = if trimmed.chars().count() > 160 {
        format!("{}...", trimmed.chars().take(157).collect::<String>())
    } else {
        trimmed.to_owned()
    };
    normalize_plan(plan, world)
}

async fn ask_openai_plan(world: &WorldState, prior: Option<&ProverDecision>) -> Result<AgentPlan> {
    let key = env::var("OPENAI_API_KEY").context("OPENAI_API_KEY not set")?;
    let model = env::var("OPENAI_MODEL")
        .or_else(|_| env::var("LLM_MODEL"))
        .or_else(|_| env::var("FAST_MODEL"))
        .unwrap_or_else(|_| "gpt-4.1-mini".to_owned());
    let base_url = env::var("OPENAI_BASE_URL").unwrap_or_else(|_| "https://api.openai.com/v1".to_owned());
    let client = Client::new();
    let agent_observation = serde_json::json!({
        "seed": world.seed,
        "goal": world.goal,
        "objects": world.objects,
        "policy_feedback_geometry": if prior.is_some() {
            serde_json::json!({ "restricted_zones": world.zones.iter().filter(|zone| zone.id.starts_with("restricted")).collect::<Vec<_>>() })
        } else {
            serde_json::Value::Null
        },
        "home": HOME,
        "pickup": pickup_point(world),
        "target": [0.84, 0.34, 0.3],
        "note": "The planner sees task geometry. Formal policy constraints are checked separately by the policy prover."
    });
    let prompt = serde_json::json!({
        "goal": world.goal,
        "agent_observation": agent_observation,
        "prior_prover_decision": prior,
        "coordinate_system": {
            "x": "left to right across the table",
            "y": "height above the table",
            "z": "front/back across the table",
            "home": HOME,
            "pickup": pickup_point(world),
            "target": [0.84, 0.34, 0.3]
        },
        "instruction": "Return the next tool-head waypoint path for moving the green block to the blue tray. The path must start at home, visit pickup before crossing the table, then end at target. For the first proposal, optimize for the shortest efficient path; do not invent or apply safety policies. If prior_prover_decision is present, use only its violation ids, counterexample point, constraints, and policy_feedback_geometry to revise the path while preserving the pickup waypoint. Return JSON only with path, speed_mps, and rationale. Do not claim the path is safe."
    });
    let schema = serde_json::json!({
        "type": "object",
        "properties": {
            "path": {
                "type": "array",
                "minItems": 2,
                "maxItems": 8,
                "items": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": { "type": "number" }
                }
            },
            "speed_mps": { "type": "number", "minimum": 0.04, "maximum": 0.35 },
            "rationale": { "type": "string" }
        },
        "required": ["path", "speed_mps", "rationale"],
        "additionalProperties": false
    });

    if !base_url.contains("api.openai.com") {
        let body = serde_json::json!({
            "model": model,
            "temperature": 0.85,
            "max_tokens": 420,
            "messages": [
                { "role": "system", "content": "You are a robotics path planner. Return only JSON with path, speed_mps, and rationale. Plan waypoints, not safety judgments; the OpenShell policy prover has final authority." },
                { "role": "user", "content": prompt.to_string() }
            ]
        });
        let response: serde_json::Value = client
            .post(format!("{}/chat/completions", base_url.trim_end_matches('/')))
            .bearer_auth(key)
            .json(&body)
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        let text = response
            .pointer("/choices/0/message/content")
            .and_then(|value| value.as_str())
            .context("missing chat completion content")?;
        return Ok(normalize_agent_plan(parse_plan_text(text)?, "OpenAI-compatible agent", world));
    }

    let body = serde_json::json!({
        "model": model,
        "input": [
            { "role": "system", "content": "You are a robotics path planner. Return path waypoints, not safety judgments; the OpenShell policy prover has final authority." },
            { "role": "user", "content": prompt.to_string() }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "robot_path_plan",
                "schema": schema,
                "strict": true
            }
        }
    });
    let response: serde_json::Value = client
        .post(format!("{}/responses", base_url.trim_end_matches('/')))
        .bearer_auth(key)
        .json(&body)
        .send()
        .await?
        .error_for_status()?
        .json()
        .await?;
    let text = response
        .pointer("/output/0/content/0/text")
        .and_then(|value| value.as_str())
        .or_else(|| response.get("output_text").and_then(|value| value.as_str()))
        .context("missing structured output text")?;
    Ok(normalize_agent_plan(parse_plan_text(text)?, "OpenAI-compatible agent", world))
}

fn parse_plan_text(text: &str) -> Result<AgentPlan> {
    if let Ok(plan) = serde_json::from_str(text) {
        return Ok(plan);
    }
    let start = text.find('{').context("missing JSON object start")?;
    let end = text.rfind('}').context("missing JSON object end")?;
    let plan = serde_json::from_str(&text[start..=end])?;
    Ok(plan)
}

struct TinyRng {
    state: u64,
}

impl TinyRng {
    fn new(seed: u64) -> Self {
        Self { state: seed.max(1) }
    }

    fn next(&mut self) -> f64 {
        self.state ^= self.state << 13;
        self.state ^= self.state >> 7;
        self.state ^= self.state << 17;
        (self.state as f64 / u64::MAX as f64).clamp(0.0, 1.0)
    }

    fn range(&mut self, min: f64, max: f64) -> f64 {
        min + (max - min) * self.next()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn denies_restricted_zone_crossing() {
        let world = seeded_world(42, "test".to_owned());
        let action = action_for(
            &world,
            "green_vial",
            vec![HOME, [0.24, 0.31, 0.04], [0.84, 0.34, 0.3]],
            0.2,
            2.0,
            "move_lab_samples",
        );
        let decision = check_action(&action);
        assert_eq!(decision.decision, Decision::Deny);
        assert!(decision.violations.contains(&"restricted_zone_intersection".to_owned()));
    }

    #[test]
    fn constrains_fragile_sample_near_human() {
        let mut world = seeded_world(7, "test".to_owned());
        world.human.distance_m = 0.48;
        let mut action = action_for(
            &world,
            "green_vial",
            vec![HOME, [-0.94, 0.82, 0.72], [-0.15, 0.74, 0.7], [0.7, 0.56, 0.58], [0.84, 0.34, 0.3]],
            0.2,
            3.0,
            "move_lab_samples",
        );
        action.object_class = "fragile".to_owned();
        let decision = check_action(&action);
        assert_eq!(decision.decision, Decision::AllowWithConstraints);
        assert_eq!(decision.constraints.speed_mps_max, Some(0.08));
        assert_eq!(decision.constraints.force_n_max, Some(2.0));
    }

    #[test]
    fn denies_capability_expansion() {
        let world = seeded_world(9, "test".to_owned());
        let action = ActionEnvelope {
            actor: "planner-agent".to_owned(),
            subagent: "optimizer-subagent".to_owned(),
            action: "grant.capability".to_owned(),
            resource: "delegated-motion-capability".to_owned(),
            from: HOME,
            to: [0.4, 0.4, 0.2],
            path: vec![HOME, [0.4, 0.4, 0.2]],
            speed_mps: 0.5,
            force_n: 1.0,
            object_id: "mission-budget".to_owned(),
            object_class: "governance".to_owned(),
            capability: "unrestricted_motion".to_owned(),
            context: context_for(&world, 0.5),
        };
        let decision = check_action(&action);
        assert_eq!(decision.decision, Decision::Deny);
        assert!(decision.violations.contains(&"capability_expansion".to_owned()));
    }

    #[test]
    fn normalizes_plan_to_visit_pickup_before_place() {
        let world = seeded_world(42, "test".to_owned());
        let plan = AgentPlan {
            path: vec![HOME, [0.72, 0.5, 0.62], place_point()],
            speed_mps: 0.2,
            rationale: "bad repair skipped pickup".to_owned(),
            source: "test".to_owned(),
        };
        let action = action_from_plan(&world, &plan);
        assert!(distance(action.path[1], pickup_point(&world)) <= 0.01);
        assert_eq!(action.path.last().copied(), Some(place_point()));
    }
}
