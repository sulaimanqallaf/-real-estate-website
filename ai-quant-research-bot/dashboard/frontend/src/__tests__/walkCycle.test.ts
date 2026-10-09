import { describe, expect, it } from "vitest";
import { computeWalkPose } from "../pixi/walkCycle";

describe("computeWalkPose", () => {
  it("has zero limb swing while idle", () => {
    const pose = computeWalkPose(5, false);
    expect(pose.leftLegOffset).toBe(0);
    expect(pose.rightLegOffset).toBe(0);
    expect(pose.leftArmOffset).toBe(0);
    expect(pose.rightArmOffset).toBe(0);
  });

  it("bobs gently (small, nonzero at most phases) while idle", () => {
    const pose = computeWalkPose(13, false);
    expect(Math.abs(pose.bodyBob)).toBeLessThan(2);
  });

  it("swings legs in opposite directions while walking (a real gait, not both legs together)", () => {
    const pose = computeWalkPose(3, true);
    expect(pose.leftLegOffset).toBeCloseTo(-pose.rightLegOffset, 5);
    expect(pose.leftLegOffset).not.toBe(0);
  });

  it("pairs each arm opposite its same-side leg (natural contralateral gait)", () => {
    const pose = computeWalkPose(7, true);
    expect(Math.sign(pose.leftArmOffset)).not.toBe(Math.sign(pose.leftLegOffset));
  });

  it("oscillates smoothly as phase advances (walking)", () => {
    const poses = [0, 1, 2, 3, 4, 5].map((p) => computeWalkPose(p, true).leftLegOffset);
    const distinctSigns = new Set(poses.map((v) => Math.sign(v)));
    expect(distinctSigns.size).toBeGreaterThan(1); // the swing actually crosses zero over time
  });

  it("is a pure function of its inputs (same phase/isWalking -> same pose)", () => {
    expect(computeWalkPose(42, true)).toEqual(computeWalkPose(42, true));
    expect(computeWalkPose(42, false)).toEqual(computeWalkPose(42, false));
  });
});
