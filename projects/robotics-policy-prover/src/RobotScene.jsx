import { Canvas, useFrame } from "@react-three/fiber";
import { ContactShadows, Edges, Line, OrbitControls } from "@react-three/drei";
import { Suspense, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { homePosition } from "./scenarios.js";

const gantryHome = new THREE.Vector3(-1.08, 0.98, 0.58);

function toVector(point) {
  return new THREE.Vector3(point[0], point[1], point[2]);
}

function samplePath(points, progress) {
  if (!points.length) return toVector(homePosition);
  if (points.length === 1) return toVector(points[0]);

  const scaled = THREE.MathUtils.clamp(progress, 0, 1) * (points.length - 1);
  const index = Math.min(Math.floor(scaled), points.length - 2);
  const local = scaled - index;
  return toVector(points[index]).lerp(toVector(points[index + 1]), local);
}

function segmentQuaternion(start, end) {
  const direction = new THREE.Vector3().subVectors(end, start);
  const quaternion = new THREE.Quaternion();
  quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.clone().normalize());
  return quaternion;
}

function Segment({ start, end, radius = 0.045, color = "#54616f" }) {
  const startVec = useMemo(() => toVector(start), [start]);
  const endVec = useMemo(() => toVector(end), [end]);
  const midpoint = useMemo(() => startVec.clone().lerp(endVec, 0.5), [startVec, endVec]);
  const length = useMemo(() => startVec.distanceTo(endVec), [startVec, endVec]);
  const quaternion = useMemo(() => segmentQuaternion(startVec, endVec), [startVec, endVec]);

  return (
    <mesh position={midpoint} quaternion={quaternion} castShadow receiveShadow>
      <cylinderGeometry args={[radius, radius, length, 28]} />
      <meshStandardMaterial color={color} metalness={0.35} roughness={0.42} />
    </mesh>
  );
}

function ToolHead({ step, playbackSpeed }) {
  const path = step.approvedPath?.length > 1 ? step.approvedPath : [homePosition];
  const [tip, setTip] = useState(() => toVector(path[0]));
  const progress = useRef(0);
  const pausePulse = step.decision === "deny" || step.decision === "pending";

  useFrame((_, delta) => {
    const pace = step.decision === "deny" ? 0 : playbackSpeed;
    progress.current = (progress.current + delta * 0.18 * pace) % 1;
    const next = samplePath(path, pausePulse ? 0 : progress.current);
    setTip(next);
  });

  const lift = useMemo(() => tip.clone().setY(gantryHome.y), [tip]);
  const tipArray = tip.toArray();
  const liftArray = lift.toArray();

  return (
    <group>
      <Segment start={[-1.34, gantryHome.y, 0.86]} end={[1.24, gantryHome.y, 0.86]} radius={0.025} color="#3a4149" />
      <Segment start={[-1.34, gantryHome.y, -0.94]} end={[1.24, gantryHome.y, -0.94]} radius={0.025} color="#3a4149" />
      <Segment start={[-1.28, 0.02, 0.86]} end={[-1.28, gantryHome.y, 0.86]} radius={0.03} color="#3a4149" />
      <Segment start={[1.18, 0.02, 0.86]} end={[1.18, gantryHome.y, 0.86]} radius={0.03} color="#3a4149" />
      <Segment start={[-1.28, 0.02, -0.94]} end={[-1.28, gantryHome.y, -0.94]} radius={0.03} color="#3a4149" />
      <Segment start={[1.18, 0.02, -0.94]} end={[1.18, gantryHome.y, -0.94]} radius={0.03} color="#3a4149" />
      <Segment start={liftArray} end={tipArray} radius={0.026} color="#262b31" />
      <mesh position={liftArray} castShadow>
        <boxGeometry args={[0.18, 0.1, 0.18]} />
        <meshStandardMaterial color="#e5e9ef" metalness={0.35} roughness={0.3} />
      </mesh>
      <mesh position={tipArray} castShadow>
        <sphereGeometry args={[0.075, 32, 32]} />
        <meshStandardMaterial color={step.decision === "deny" ? "#e45454" : "#37ba78"} />
      </mesh>
      <Segment
        start={[tip.x - 0.055, tip.y - 0.02, tip.z]}
        end={[tip.x - 0.15, tip.y - 0.08, tip.z]}
        radius={0.018}
        color="#25292f"
      />
      <Segment
        start={[tip.x + 0.055, tip.y - 0.02, tip.z]}
        end={[tip.x + 0.15, tip.y - 0.08, tip.z]}
        radius={0.018}
        color="#25292f"
      />
    </group>
  );
}

function carriedPosition(object, path, progress) {
  if (object.id !== "green_vial" || !path?.length || path.length < 2) return object.position;
  const original = toVector(object.position);
  const pickupIndex = path.reduce(
    (best, point, index) => {
      const distance = toVector(point).distanceTo(original);
      return distance < best.distance ? { index, distance } : best;
    },
    { index: 0, distance: Infinity },
  );
  const scaled = THREE.MathUtils.clamp(progress, 0, 1) * (path.length - 1);
  if (scaled < pickupIndex.index) return object.position;
  const tip = samplePath(path, progress);
  return [tip.x, Math.max(object.position[1], tip.y - 0.18), tip.z];
}

function WorkcellObjects({ objects, step, playbackSpeed }) {
  const path = step.approvedPath?.length > 1 ? step.approvedPath : [];
  const [progress, setProgress] = useState(0);
  const progressRef = useRef(0);

  useFrame((_, delta) => {
    const shouldMove = path.length > 1 && step.decision !== "deny" && step.decision !== "pending";
    const pace = shouldMove ? playbackSpeed : 0;
    progressRef.current = (progressRef.current + delta * 0.18 * pace) % 1;
    setProgress(progressRef.current);
  });

  return (
    <group>
      {objects.map((object) => (
        <mesh key={object.id} position={carriedPosition(object, path, progress)} castShadow receiveShadow>
          <boxGeometry args={object.size} />
          <meshStandardMaterial color={object.color} roughness={0.36} metalness={0.05} />
          <Edges color="#1f2228" />
        </mesh>
      ))}
    </group>
  );
}

function Zone({ zone }) {
  return (
    <mesh position={zone.position} receiveShadow>
      <boxGeometry args={zone.size} />
      <meshStandardMaterial color={zone.color} transparent opacity={0.16} depthWrite={false} />
      <Edges color={zone.color} />
    </mesh>
  );
}

function MotionPath({ points, color, opacity = 1, dashed = false }) {
  if (!points?.length || points.length < 2) return null;
  return (
    <Line
      points={points}
      color={color}
      lineWidth={dashed ? 1.5 : 4}
      transparent
      opacity={opacity}
      dashed={dashed}
      dashScale={0.7}
      dashSize={0.2}
      gapSize={0.12}
    />
  );
}

function HumanPresence({ human, decision }) {
  const warning = decision === "allow_with_constraints" || decision === "approval_required";
  return (
    <group position={human.position}>
      <mesh position={[0, 0.05, 0]} castShadow>
        <sphereGeometry args={[human.radius, 32, 32]} />
        <meshStandardMaterial color={warning ? "#e8b630" : "#63b3ed"} roughness={0.4} />
      </mesh>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.012, 0]}>
        <ringGeometry args={[human.radius + 0.04, human.caution, 64]} />
        <meshBasicMaterial
          color={warning ? "#e8b630" : "#63b3ed"}
          transparent
          opacity={warning ? 0.22 : 0.12}
          side={THREE.DoubleSide}
        />
      </mesh>
    </group>
  );
}

function SceneContent({ world, step, playbackSpeed }) {
  const approvedColor =
    step.decision === "deny"
      ? "#e45454"
      : step.decision === "approval_required" || step.decision === "allow_with_constraints"
        ? "#e8b630"
        : "#32bd77";

  return (
    <>
      <color attach="background" args={["#eef1ed"]} />
      <ambientLight intensity={0.75} />
      <directionalLight position={[3.2, 5.4, 2.8]} intensity={2.6} castShadow />
      <directionalLight position={[-4, 2.8, -3]} intensity={0.55} color="#87d6cf" />
      <group position={[0, 0, 0]}>
        <mesh position={[0, -0.015, 0]} receiveShadow>
          <boxGeometry args={[3.3, 0.03, 2.2]} />
          <meshStandardMaterial color="#d8ded8" roughness={0.65} />
        </mesh>
        <gridHelper args={[3.2, 16, "#9aa59d", "#c5ccc3"]} position={[0, 0.006, 0]} />
        {world.zones.map((zone) => (
          <Zone key={zone.id} zone={zone} />
        ))}
        <WorkcellObjects key={`objects-${step.id}`} objects={world.objects} step={step} playbackSpeed={playbackSpeed} />
        <HumanPresence human={world.human} decision={step.decision} />
        <MotionPath points={step.proposedPath} color="#e45454" opacity={0.55} dashed />
        <MotionPath points={step.approvedPath} color={approvedColor} opacity={0.92} />
        <ToolHead key={step.id} step={step} playbackSpeed={playbackSpeed} />
      </group>
      <ContactShadows position={[0, -0.004, 0]} opacity={0.28} scale={4.4} blur={2.4} far={2.8} />
      <OrbitControls
        enablePan={false}
        minDistance={2.35}
        maxDistance={4.9}
        minPolarAngle={0.62}
        maxPolarAngle={1.24}
        target={[0, 0.22, -0.08]}
      />
    </>
  );
}

export default function RobotScene({ world, step, playbackSpeed }) {
  return (
    <Canvas
      shadows
      camera={{ position: [2.65, 2.05, 2.55], fov: 42 }}
      gl={{ antialias: true, preserveDrawingBuffer: true }}
    >
      <Suspense fallback={null}>
        <SceneContent world={world} step={step} playbackSpeed={playbackSpeed} />
      </Suspense>
    </Canvas>
  );
}
