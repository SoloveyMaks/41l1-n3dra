import React, { memo } from 'react';
import { EdgeProps, getBezierPath } from '@xyflow/react';
import type { EdgeMotionState } from '../execution/runTelemetry';

export interface ExecutionEdgeData {
  flow?: EdgeMotionState;
  pulse?: boolean;
  tone?: 'true' | 'false' | 'default';
  [key: string]: unknown;
}

const TONE_COLOR: Record<NonNullable<ExecutionEdgeData['tone']>, string> = {
  true: '#34d399',
  false: '#f87171',
  default: '#818cf8',
};

export const ExecutionEdge = memo(({
  id,
  sourceX,
  sourceY,
  sourcePosition,
  targetX,
  targetY,
  targetPosition,
  data,
}: EdgeProps) => {
  const edgeData = (data || {}) as ExecutionEdgeData;
  const flow = edgeData.flow || 'idle';
  const tone = edgeData.tone || 'default';
  const color = TONE_COLOR[tone];
  const pulse = Boolean(edgeData.pulse) && flow === 'flowing';

  const [path] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  return (
    <g
      className={`exec-edge exec-edge--${flow}`}
      data-edge-id={id}
      data-flow={flow}
      data-tone={tone}
      style={{ ['--exec-edge-color' as string]: color } as React.CSSProperties}
    >
      {/* Wide transparent path: keeps the edge clickable without a visible hit box. */}
      <path d={path} className="exec-edge__hit" fill="none" stroke="transparent" strokeWidth={18} />
      <path d={path} className="exec-edge__glow" fill="none" />
      <path d={path} className="exec-edge__base" fill="none" />
      {pulse && (
        <>
          <circle r={4.5} className="exec-edge__dot">
            <animateMotion dur="1.5s" repeatCount="indefinite" path={path} />
          </circle>
          <circle r={2.8} className="exec-edge__dot exec-edge__dot--trail">
            <animateMotion dur="1.5s" begin="-0.75s" repeatCount="indefinite" path={path} />
          </circle>
        </>
      )}
    </g>
  );
});

ExecutionEdge.displayName = 'ExecutionEdge';