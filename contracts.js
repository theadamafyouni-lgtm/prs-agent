/**
 * Read-only boundary for the JSON artifacts described in agent/.claude/skills.
 * This validates shape and cross-artifact consistency; it makes no clinical or
 * scientific judgments and never computes a score, percentile, or ancestry.
 */
import { createSessionState, applySessionEvent } from './session.js';

export const STAGES = Object.freeze([
  'read-and-reason', 'ancestry', 'select', 'score', 'interpret', 'report',
]);

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const text = value => typeof value === 'string' && value.trim().length > 0;
const number = value => typeof value === 'number' && Number.isFinite(value);
const fraction = value => number(value) && value >= 0 && value <= 1;
const percentile = value => number(value) && value >= 0 && value <= 100;
const issue = (path, code, message) => ({ path, code, message });
const hasEvidence = artifact => Array.isArray(artifact?.evidence) && artifact.evidence.length > 0
  && artifact.evidence.every(item => object(item) && text(item.source));
const hasTextList = value => Array.isArray(value) && value.length > 0 && value.every(text);

/** `blocked` and voluntary ending are orchestration states, not agent refusals. */
export function normalizeOutcome(stage, outcome) {
  if (['refuse', 'stop', 'ask', 'blocked', 'session-ended-by-person'].includes(outcome)) return outcome;
  if (outcome === 'proceed') return stage === 'report' ? 'unknown' : 'continue';
  if (outcome === 'score') return stage === 'report' ? 'score' : 'continue';
  return 'unknown';
}

/** Canonical bundle plus explicit aliases for the skill's file/stage names. */
export function readArtifacts(bundle = {}) {
  if (!object(bundle)) bundle = {};
  const stages = object(bundle.stages) ? bundle.stages : {};
  return {
    classification: bundle.classification ?? stages['read-and-reason'] ?? stages.door,
    ancestry: bundle.ancestry ?? stages.ancestry,
    model_choice: bundle.model_choice ?? stages.select,
    score: bundle.score ?? stages.score,
    coverage: bundle.coverage ?? bundle.measured_coverage,
    interpret: bundle.interpret ?? stages.interpret,
    report: bundle.report ?? stages.report,
    reference_response: bundle.reference_response,
  };
}

function validateJudgment(value, path, stage) {
  if (!object(value)) return [issue(path, 'object_required', 'The stage artifact must be a JSON object.')];
  const issues = [];
  if (normalizeOutcome(stage, value.outcome) === 'unknown') {
    issues.push(issue(`${path}.outcome`, 'unknown_outcome', 'A recognized stage outcome is required.'));
  }
  if (!text(value.decision)) issues.push(issue(`${path}.decision`, 'decision_required', 'The agent must explain its decision.'));
  if (!hasTextList(value.reasons)) issues.push(issue(`${path}.reasons`, 'reasons_required', 'The judgment must carry its reasons.'));
  if (!hasEvidence(value)) issues.push(issue(`${path}.evidence`, 'evidence_required', 'The judgment must identify its source artifacts.'));
  if (['refuse', 'stop', 'ask'].includes(value.outcome) && !hasTextList(value.requirement_ids)) {
    issues.push(issue(`${path}.requirement_ids`, 'requirements_required', 'This outcome must identify the requirements behind it.'));
  }
  return issues;
}

function validateRefusal(report) {
  const envelope = report.refusal_envelope;
  if (!object(envelope)) return [issue('report.refusal_envelope', 'envelope_required', 'The refusal needs the person-facing refusal envelope.')];
  const issues = [];
  const fields = ['headline', 'detail', 'what_was_tried', 'why_this_is_honest', 'what_would_change_it'];
  for (const field of fields) {
    if (!text(envelope[field])) issues.push(issue(`report.refusal_envelope.${field}`, 'copy_required', 'The refusal must include this person-facing explanation.'));
  }
  if (typeof envelope.is_about_the_person !== 'boolean') {
    issues.push(issue('report.refusal_envelope.is_about_the_person', 'scope_required', 'The envelope must say whether the limitation is about the person.'));
  }
  const prose = fields.map(field => typeof envelope[field] === 'string' ? envelope[field] : '').join(' ');
  // Narrow checks for obvious recording language; a machine cannot certify tone.
  if (/\bREP-\d+\b|\b(?:told|explained to|informed) (?:them|the patient|the person)\b|\bnamed specifically:/i.test(prose)) {
    issues.push(issue('report.refusal_envelope', 'log_register', 'Use the envelope as direct delivery to the person, not a record of what was said.'));
  }
  if (prose && !/\byou(?:r|rs|'re|'ve|'ll)?\b/i.test(prose)) {
    issues.push(issue('report.refusal_envelope', 'direct_address_required', 'The refusal envelope should address you directly.'));
  }
  return issues;
}

function validateReference(response) {
  const path = 'reference_response';
  if (!object(response) || response.status !== 'reference_valid' || response.interpret_can_proceed !== true) {
    return [issue(path, 'validated_reference_required', 'A validated, usable reference response is required before displaying a score.')];
  }
  const ref = response.reference;
  if (!object(ref) || ref.usable !== true) {
    return [issue(`${path}.reference`, 'usable_reference_required', 'The provider must explicitly report a usable reference.')];
  }
  const issues = [];
  for (const field of ['source', 'version_string', 'ancestry_grouping_definition', 'cohort_construction_rule', 'assay_layer']) {
    const structured = !['source', 'version_string'].includes(field) && object(ref[field]) && Object.keys(ref[field]).length;
    if (!text(ref[field]) && !structured) {
      issues.push(issue(`${path}.reference.${field}`, 'reference_metadata_required', `The reference must identify ${field}.`));
    }
  }
  if (!Number.isInteger(ref.n_participants) || ref.n_participants <= 0) {
    issues.push(issue(`${path}.reference.n_participants`, 'participant_count_required', 'The provider must report its actual participant count.'));
  }
  if (!number(ref.too_few_threshold_used) || ref.too_few_threshold_used < 0) {
    issues.push(issue(`${path}.reference.too_few_threshold_used`, 'threshold_required', 'The provider must state the minimum cohort size it used.'));
  } else if (number(ref.n_participants) && ref.n_participants < ref.too_few_threshold_used) {
    issues.push(issue(`${path}.reference.n_participants`, 'reference_contradiction', 'The reported usable cohort is below the provider’s own minimum.'));
  }
  if (!['same-resolved-set', 'full-model-set', 'other'].includes(ref.scoring_parity)) {
    issues.push(issue(`${path}.reference.scoring_parity`, 'scoring_parity_required', 'The provider must state how its scoring compares with the person’s scoring.'));
  }
  const distribution = ref.distribution;
  if (!object(distribution)) {
    issues.push(issue(`${path}.reference.distribution`, 'distribution_required', 'The validated reference distribution must be present.'));
  } else {
    const raw = Array.isArray(distribution.raw_scores) && distribution.raw_scores.length > 0 && distribution.raw_scores.every(number);
    const quantiles = ['p01', 'p05', 'p10', 'p25', 'p50', 'p75', 'p90', 'p95', 'p99'].every(key => number(distribution[key]));
    if (!raw && !quantiles) issues.push(issue(`${path}.reference.distribution`, 'distribution_incomplete', 'The provider must supply its scores or the full required quantile set.'));
    for (const field of ['mean', 'sd', 'n']) {
      if (!number(distribution[field])) issues.push(issue(`${path}.reference.distribution.${field}`, 'distribution_metadata_required', 'The reference distribution must include mean, standard deviation and size.'));
    }
    if (number(distribution.sd) && distribution.sd <= 0) issues.push(issue(`${path}.reference.distribution.sd`, 'distribution_invalid', 'A usable distribution needs a positive standard deviation.'));
    if (number(distribution.n) && (!Number.isInteger(distribution.n) || distribution.n <= 0)) issues.push(issue(`${path}.reference.distribution.n`, 'distribution_invalid', 'The distribution size must be a positive integer.'));
  }
  if (!Array.isArray(response.warnings_for_report) || !response.warnings_for_report.every(text)) {
    issues.push(issue(`${path}.warnings_for_report`, 'warnings_required', 'The adapter’s report warnings must be supplied, including an empty list when there are none.'));
  }
  return issues;
}

function measuredCoverage(coverage) {
  if (!object(coverage)) return false;
  if (coverage.is_an_estimate === true || coverage.measured_not_estimated === false) return false;
  if (coverage.tool === 'weight_coverage.py measure') return true;
  // Explicit bridge form for a backend that attaches source provenance to artifacts.
  return coverage.measured_not_estimated === true && text(coverage.source)
    && /(?:^|\/)score\/coverage_(?!est_)[^/]+\.json$/.test(coverage.source)
    && (!text(coverage.tool) || !/estimate/i.test(coverage.tool));
}

function numericScore(report, score) {
  const delivered = report?.delivered?.score;
  if (number(delivered)) return delivered;
  if (object(delivered) && number(delivered.value)) return delivered.value;
  return number(score?.raw_score) ? score.raw_score : null;
}

function deliveredPercentile(report) {
  const placement = report?.delivered?.placement;
  if (number(placement)) return placement;
  return object(placement) && number(placement.percentile) ? placement.percentile : null;
}

export function validateReport(report, context = {}) {
  const issues = validateJudgment(report, 'report', 'report');
  if (!object(report)) return issues;
  if (!['score', 'refuse'].includes(report.outcome)) {
    issues.push(issue('report.outcome', 'final_report_outcome', 'A completed report is either a score or a refusal; questions and blocked jobs remain open.'));
    return issues;
  }
  if (report.outcome === 'refuse') return [...issues, ...validateRefusal(report)];
  const { interpret, coverage, score, reference_response, model_choice } = object(context) ? context : {};
  issues.push(...validateReference(reference_response));
  if (!object(report.delivered)) {
    issues.push(issue('report.delivered', 'delivery_required', 'The score needs its delivered explanation and limits.'));
  } else {
    for (const field of ['personalized_limit', 'disclaimer']) {
      if (!text(report.delivered[field])) issues.push(issue(`report.delivered.${field}`, 'explanation_required', 'The report must include its personalized limit and disclaimer.'));
    }
  }
  if (!object(interpret) || interpret.outcome !== 'score' || !hasEvidence(interpret)) {
    issues.push(issue('interpret', 'interpretation_required', 'A recorded interpretation with source evidence is required.'));
  }
  if (!percentile(interpret?.percentile)) issues.push(issue('interpret.percentile', 'percentile_required', 'The interpreted percentile must be a finite number from 0 to 100.'));
  if (object(interpret?.reference) && object(reference_response?.reference)) {
    for (const [summaryKey, providerKey] of [['source', 'source'], ['version_string', 'version_string'], ['n', 'n_participants'], ['scoring_parity', 'scoring_parity']]) {
      if (interpret.reference[summaryKey] !== undefined && interpret.reference[summaryKey] !== reference_response.reference[providerKey]) {
        issues.push(issue(`interpret.reference.${summaryKey}`, 'reference_mismatch', 'The interpretation disagrees with the provider reference metadata.'));
      }
    }
  }
  const placement = deliveredPercentile(report);
  if (placement !== null && placement !== interpret?.percentile) {
    issues.push(issue('report.delivered.placement', 'placement_mismatch', 'The delivered percentile disagrees with the interpretation.'));
  }
  if (!measuredCoverage(coverage)) issues.push(issue('coverage', 'measured_coverage_required', 'Use the measured score/coverage artifact, never the selection estimate.'));
  if (!fraction(coverage?.resolved_weight_fraction)) issues.push(issue('coverage.resolved_weight_fraction', 'coverage_invalid', 'Measured weight coverage must be a finite fraction from 0 to 1.'));
  if (!fraction(report.delivered?.resolved_weight_fraction)) issues.push(issue('report.delivered.resolved_weight_fraction', 'coverage_required', 'The delivered report must carry measured weight coverage.'));
  else if (report.delivered.resolved_weight_fraction !== coverage?.resolved_weight_fraction) {
    issues.push(issue('report.delivered.resolved_weight_fraction', 'coverage_mismatch', 'The delivered coverage disagrees with the measured artifact.'));
  }
  if (numericScore(report, score) === null) issues.push(issue('report.delivered.score', 'score_required', 'A finite recorded score is required; display never derives or invents one.'));
  if (number(score?.raw_score) && numericScore(report, score) !== score.raw_score) issues.push(issue('report.delivered.score', 'score_mismatch', 'The delivered score disagrees with the scoring artifact.'));
  if (score && (score.outcome && score.outcome !== 'score' || text(score.internal_consistency) && /INCONSISTENT/i.test(score.internal_consistency) || number(score.shortfall) && score.shortfall !== 0)) {
    issues.push(issue('score', 'scoring_inconsistent', 'The scoring artifact does not support a completed score.'));
  }
  const modelIds = [
    ['model_choice.chosen_model', model_choice?.chosen_model],
    ['score.model', score?.model],
    ['reference_response.request_echo.model.pgs_id', reference_response?.request_echo?.model?.pgs_id],
  ].filter(([, value]) => value !== undefined && value !== null);
  if (!modelIds.length) issues.push(issue('score.model', 'score_model_required', 'A score must identify the model it was computed with.'));
  for (const [path, value] of modelIds) {
    if (!text(value) || !/^PGS\d{6}$/.test(value)) issues.push(issue(path, 'model_id_required', 'The scoring model must be one bare PGS identifier.'));
    else if (modelIds.some(([, other]) => other !== value)) issues.push(issue(path, 'model_identity_mismatch', 'The model identities disagree across the score, choice and reference request.'));
  }
  const request = reference_response?.request_echo;
  if (object(request?.patient)) {
    for (const [key, expected] of [['raw_score', numericScore(report, score)], ['resolved_weight_fraction', coverage?.resolved_weight_fraction]]) {
      if (request.patient[key] !== undefined && request.patient[key] !== expected) issues.push(issue(`reference_response.request_echo.patient.${key}`, 'reference_request_mismatch', 'The reference was requested for different score or coverage values.'));
    }
  }
  if (text(model_choice?.trait_id) && request?.trait?.id !== undefined && request.trait.id !== model_choice.trait_id) issues.push(issue('reference_response.request_echo.trait.id', 'trait_identity_mismatch', 'The reference request and model choice identify different traits.'));
  if (!Array.isArray(interpret?.caveats_for_report) || !interpret.caveats_for_report.every(text)) issues.push(issue('interpret.caveats_for_report', 'caveats_required', 'The interpretation’s report caveats must be supplied.'));
  return issues;
}

/** Partial bundles are valid; absent stages are pending, never fabricated. */
export function validateArtifactBundle(bundle) {
  if (!object(bundle)) return [issue('bundle', 'object_required', 'Import a JSON object containing stage artifacts.')];
  const a = readArtifacts(bundle);
  const issues = [];
  const mapping = { classification: 'read-and-reason', ancestry: 'ancestry', model_choice: 'select', score: 'score', interpret: 'interpret' };
  for (const [name, stage] of Object.entries(mapping)) {
    if (a[name] === undefined) continue;
    // raw_score.json is a mechanical facts artifact; score.json is a judgment.
    if (name === 'score' && a[name]?.tool === 'score_plink2.py' && a[name]?.outcome === undefined) continue;
    issues.push(...validateJudgment(a[name], name, stage));
  }
  const c = a.classification;
  if (c && normalizeOutcome('read-and-reason', c.outcome) === 'continue') {
    for (const key of ['assay_class', 'absence_means', 'confidence']) if (!text(c[key])) issues.push(issue(`classification.${key}`, 'classification_required', 'The file classification must explain its assay, absence semantics and confidence.'));
    if (!object(c.genome_build) || !text(c.genome_build.value) || !hasTextList(c.genome_build.verified_by)) issues.push(issue('classification.genome_build', 'build_evidence_required', 'The genome build needs its recorded verification evidence.'));
  }
  const ancestry = a.ancestry;
  if (ancestry && normalizeOutcome('ancestry', ancestry.outcome) === 'continue') {
    if (!text(ancestry.inferred_group)) issues.push(issue('ancestry.inferred_group', 'ancestry_required', 'The genetic ancestry judgment is missing.'));
    if (!['agree', 'disagree', 'no-self-report'].includes(ancestry.agreement)) issues.push(issue('ancestry.agreement', 'agreement_required', 'State whether self-report and inferred ancestry differ, or no self-report was given.'));
    if (!text(ancestry.used_for_math) || !/^inferred\b/.test(ancestry.used_for_math)) issues.push(issue('ancestry.used_for_math', 'inferred_math_required', 'Model and reference selection must use inferred genetic similarity.'));
    if (ancestry.agreement === 'disagree' && ancestry.surfaced_to_person !== true) issues.push(issue('ancestry.surfaced_to_person', 'mismatch_not_surfaced', 'The ancestry disagreement must be shown before model selection.'));
  }
  const choice = a.model_choice;
  if (choice && normalizeOutcome('select', choice.outcome) === 'continue') {
    if (!text(choice.chosen_model) || !/^PGS\d{6}$/.test(choice.chosen_model)) issues.push(issue('model_choice.chosen_model', 'model_id_required', 'The chosen model must be one bare PGS identifier.'));
    if (!text(choice.trait_id) || !/^[A-Za-z]+[_:]\d+$/.test(choice.trait_id)) issues.push(issue('model_choice.trait_id', 'trait_id_required', 'The resolved trait must be one bare ontology identifier.'));
    if (!['transparency', 'decision'].includes(choice.gate_type)) issues.push(issue('model_choice.gate_type', 'gate_required', 'The model choice must distinguish acknowledgment from a value decision.'));
    if (!Array.isArray(choice.candidates_considered) || !choice.candidates_considered.length || choice.candidates_considered.some(candidate => !object(candidate) || !text(candidate.pgs_id) || !text(candidate.why_ranked_here) || !text(candidate.cost_of_choosing_it))) issues.push(issue('model_choice.candidates_considered', 'candidate_cost_required', 'Each recommendation must show its reasoning and cost.'));
    if (Array.isArray(choice.candidates_considered) && !choice.candidates_considered.some(candidate => object(candidate) && candidate.pgs_id === choice.chosen_model)) issues.push(issue('model_choice.chosen_model', 'chosen_candidate_missing', 'The recommended model must appear in the ranked candidates with its reasoning and cost.'));
    if (!object(choice.coverage_estimate) || !fraction(choice.coverage_estimate.resolved_weight_fraction) || choice.coverage_estimate.is_an_estimate !== true) issues.push(issue('model_choice.coverage_estimate', 'estimate_label_required', 'Selection coverage must be supplied and explicitly labeled as an estimate.'));
  }
  if (a.report !== undefined) issues.push(...validateReport(a.report, a));
  if (a.report?.outcome === 'score') {
    for (const name of Object.keys(mapping)) {
      if (['refuse', 'stop', 'ask', 'blocked', 'session-ended-by-person'].includes(a[name]?.outcome)) {
        issues.push(issue(`${name}.outcome`, 'unfinished_before_report', 'A score report conflicts with an earlier stage that has not continued.'));
      }
    }
    if (Array.isArray(bundle.events) && bundle.events.length) {
      const session = bundle.events.reduce(applySessionEvent, createSessionState());
      if (['ask', 'waiting-agent', 'blocked', 'refuse', 'stop', 'error', 'session-ended-by-person'].includes(session.status)) issues.push(issue('events', 'report_event_conflict', 'The score report conflicts with the run’s latest recorded event state.'));
    }
  }
  return issues;
}

/** Safe display view. No numeric results are exposed until all report checks pass. */
export function getDisplayResult(bundle = {}) {
  if (!object(bundle)) return { kind: 'incomplete', issues: validateArtifactBundle(bundle) };
  const a = readArtifacts(bundle);
  const issues = validateArtifactBundle(bundle);
  const empty = { issues, score: null, percentile: null, coverage: null, reference: null, warnings: [], provenance: [] };
  if (bundle.session_ended_by_person === true || bundle.status === 'session-ended-by-person') return { ...empty, kind: 'session-ended-by-person' };
  if (a.report) {
    if (issues.length) return { ...empty, kind: 'incomplete' };
    if (a.report.outcome === 'refuse') return { ...empty, kind: 'refuse', report: a.report, envelope: a.report.refusal_envelope };
    return {
      ...empty, kind: 'score', report: a.report,
      model: a.model_choice?.chosen_model ?? a.score?.model ?? a.reference_response?.request_echo?.model?.pgs_id,
      score: numericScore(a.report, a.score), percentile: a.interpret.percentile,
      coverage: a.coverage.resolved_weight_fraction,
      reference: a.reference_response.reference,
      warnings: [...new Set([...a.reference_response.warnings_for_report, ...a.interpret.caveats_for_report])],
      provenance: [
        { value: 'score', source: number(a.report.delivered?.score) || number(a.report.delivered?.score?.value) ? 'report.delivered.score' : 'score.raw_score', evidence: a.report.evidence },
        { value: 'percentile', source: 'interpret.percentile', evidence: a.interpret.evidence },
        { value: 'coverage', source: a.coverage.source ?? a.coverage.tool, evidence: a.coverage.evidence ?? [] },
        { value: 'reference', source: a.reference_response.reference.source, version: a.reference_response.reference.version_string },
      ],
    };
  }
  const artifact = [a.interpret, a.score, a.model_choice, a.ancestry, a.classification].find(value => object(value) && value.outcome);
  if (artifact && ['refuse', 'stop', 'ask', 'session-ended-by-person'].includes(artifact.outcome)) return { ...empty, kind: artifact.outcome, artifact };
  if (bundle.status === 'blocked' || ['reference_unavailable', 'reference_invalid', 'request_missing'].includes(a.reference_response?.status)) return { ...empty, kind: 'blocked' };
  return { ...empty, kind: a.score || a.interpret ? 'incomplete' : 'pending' };
}
