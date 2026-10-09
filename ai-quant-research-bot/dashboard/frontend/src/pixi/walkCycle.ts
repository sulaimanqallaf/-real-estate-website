/** Pure walk-cycle math - no PixiJS, no React, unit-testable on its
 * own. `Scene.tsx` calls `computeWalkPose()` every frame to get the
 * current limb-offset pose for a humanoid sprite; drawing it is
 * Scene.tsx's job, this module only computes the numbers. */

export interface LimbPose {
  leftLegOffset: number;
  rightLegOffset: number;
  leftArmOffset: number;
  rightArmOffset: number;
  bodyBob: number;
}

const WALK_SWING_PX = 6;
const WALK_SPEED = 0.6;
const IDLE_BOB_PX = 1.5;
const IDLE_SPEED = 0.12;

/** `phase` is a monotonically-increasing counter (e.g. total elapsed
 * ticks) - the caller owns incrementing it every frame. Walking
 * produces an opposite-phase leg/arm swing (a natural gait: left leg
 * forward pairs with right arm forward); idle produces a small,
 * slow vertical bob and zero limb swing. */
export function computeWalkPose(phase: number, isWalking: boolean): LimbPose {
  if (!isWalking) {
    return {
      leftLegOffset: 0, rightLegOffset: 0, leftArmOffset: 0, rightArmOffset: 0,
      bodyBob: Math.sin(phase * IDLE_SPEED) * IDLE_BOB_PX,
    };
  }
  const swing = Math.sin(phase * WALK_SPEED) * WALK_SWING_PX;
  return {
    leftLegOffset: swing,
    rightLegOffset: -swing,
    leftArmOffset: -swing * 0.6,
    rightArmOffset: swing * 0.6,
    bodyBob: Math.abs(Math.sin(phase * WALK_SPEED)) * 1.2,
  };
}
