import type { AnalysisBundle, ComparisonDto, AnalysisSignature } from '../../docs/data-contracts/contracts.js';

export type PublishedManifestFile = { path: string; sourceHash: string; byteLength: number; mediaType: string };
export type PublishedManifest = {
  complete: true;
  workspaceId: string;
  snapshotId: string;
  hash: string;
  files: PublishedManifestFile[];
};
export type PublishedProvenance = {
  published: true;
  bundleHash: string;
  normalizationVersion: string;
  origin: 'scanner' | 'contract_scenario' | 'manual_fixture';
  expectedScopesVerified: boolean;
  domainPolicyApplicability: 'not_applicable' | 'applied' | 'unknown';
};
export type PublishedBundleEnvelope = {
  bundle: AnalysisBundle;
  manifest: PublishedManifest;
  provenance: PublishedProvenance;
};
export type ComparisonInput = AnalysisBundle | PublishedBundleEnvelope;
export type ComparisonOptions = { id?: string };

/** Deterministically derives a UUID-shaped id from immutable comparison inputs. */
export function deterministicId(seed: string): string;
/** Matches snapshot_manifest_hash PostgreSQL serialization; no source bytes. */
export function manifestHash(files: PublishedManifestFile[]): string;
/** Compares published immutable analysis facts; it never invokes a scanner. */
export function compareBundles(
  baseline: ComparisonInput,
  current: ComparisonInput,
  options?: ComparisonOptions
): ComparisonDto;

/** Proposed persistence addition, intentionally not added to the shared DTO. */
export type ComparisonProvenanceProposal = {
  baselineSignature: AnalysisSignature;
  currentSignature: AnalysisSignature;
  baselineManifestHash?: string;
  currentManifestHash?: string;
  matchingAlgorithm: 'comparison/v1';
};
