import test from 'node:test';
import assert from 'node:assert/strict';
import { STAGES, normalizeOutcome, readArtifacts, validateReport, validateArtifactBundle, getDisplayResult } from './contracts.js';

// Synthetic contract-test fixtures only. These numbers are never bundled into
// the application or shown as a person's result. They are not scientific data.
function exampleJudgment(outcome = 'score') {
  return { outcome, decision: 'Example test judgment.', reasons: ['Example test reason.'], evidence: [{ source: 'example/test-artifact.json', field: 'example' }], requirement_ids: ['EXAMPLE-1'] };
}
function exampleBundle() {
  return {
    score: { ...exampleJudgment(), raw_score: 0.25, model: 'PGS000000' },
    coverage: { tool: 'weight_coverage.py measure', resolved_weight_fraction: 0.97 },
    interpret: { ...exampleJudgment(), percentile: 60, caveats_for_report: ['Example interpretation caveat.'] },
    reference_response: {
      status: 'reference_valid', interpret_can_proceed: true, warnings_for_report: ['Example adapter warning.'],
      reference: {
        usable: true, source: 'Example test reference', version_string: 'example-version',
        ancestry_grouping_definition: 'Example grouping', cohort_construction_rule: 'Example rule',
        n_participants: 3, too_few_threshold_used: 2, assay_layer: 'Example assay', scoring_parity: 'same-resolved-set',
        distribution: { raw_scores: [-1, 0, 1], mean: 0, sd: 1, n: 3 },
      },
    },
    report: {
      ...exampleJudgment(), delivered: { score: 0.25, placement: { percentile: 60 }, resolved_weight_fraction: 0.97, personalized_limit: 'Example personalized limit.', disclaimer: 'Example disclaimer: not a diagnosis.' },
    },
  };
}
function exampleRefusal() {
  return {
    ...exampleJudgment('refuse'),
    refusal_envelope: {
      headline: 'Example: you do not receive a score here.', detail: 'Example: your result needs a usable reference.',
      what_was_tried: 'Example: your file and model were examined.', why_this_is_honest: 'Example: you deserve a comparison supported by data.',
      what_would_change_it: 'Example: you could be represented in better reference data.', is_about_the_person: false,
    },
  };
}

test('all six stages are explicit; a continuation is never a delivered score', () => {
  assert.equal(STAGES.length, 6);
  assert.equal(normalizeOutcome('ancestry', 'proceed'), 'continue');
  assert.equal(normalizeOutcome('score', 'score'), 'continue');
  assert.equal(normalizeOutcome('report', 'score'), 'score');
  assert.equal(normalizeOutcome('report', 'proceed'), 'unknown');
  for (const state of ['refuse', 'stop', 'ask', 'blocked', 'session-ended-by-person']) assert.equal(normalizeOutcome('select', state), state);
});

test('verified artifacts expose only recorded numbers and carry every warning', () => {
  const bundle = exampleBundle();
  assert.deepEqual(validateArtifactBundle(bundle), []);
  const display = getDisplayResult(bundle);
  assert.equal(display.kind, 'score');
  assert.equal(display.score, bundle.report.delivered.score);
  assert.equal(display.percentile, bundle.interpret.percentile);
  assert.equal(display.coverage, bundle.coverage.resolved_weight_fraction);
  assert.deepEqual(display.warnings, ['Example adapter warning.', 'Example interpretation caveat.']);
  assert.ok(display.provenance.every(value => value.source));
});

test('no reference means no raw score or percentile display', () => {
  const bundle = exampleBundle();
  delete bundle.reference_response;
  const display = getDisplayResult(bundle);
  assert.equal(display.kind, 'incomplete');
  assert.equal(display.score, null);
  assert.equal(display.percentile, null);
  assert.ok(display.issues.some(value => value.code === 'validated_reference_required'));
});

test('an estimate with the same fraction cannot masquerade as measured coverage', () => {
  const bundle = exampleBundle();
  bundle.coverage.tool = 'weight_coverage.py estimate';
  bundle.coverage.measured_not_estimated = true;
  bundle.coverage.source = 'runs/example/select/coverage_est_PGS000000.json';
  assert.equal(getDisplayResult(bundle).kind, 'incomplete');
  assert.ok(validateArtifactBundle(bundle).some(value => value.code === 'measured_coverage_required'));
});

test('explicit measured source bridge remains usable without rewriting values', () => {
  const bundle = exampleBundle();
  bundle.coverage = { resolved_weight_fraction: 0.97, measured_not_estimated: true, source: 'runs/example/score/coverage_PGS000000.json' };
  assert.equal(getDisplayResult(bundle).kind, 'score');
});

test('a measured tool label does not override a contradictory estimate flag', () => {
  const bundle = exampleBundle(); bundle.coverage.is_an_estimate = true;
  assert.ok(validateArtifactBundle(bundle).some(value => value.code === 'measured_coverage_required'));
  assert.equal(getDisplayResult(bundle).score, null);
});

test('cross-artifact conflicts suppress the result instead of choosing a number', () => {
  for (const mutate of [
    bundle => { bundle.report.delivered.score = 0.3; },
    bundle => { bundle.report.delivered.placement.percentile = 61; },
    bundle => { bundle.report.delivered.resolved_weight_fraction = 0.96; },
    bundle => { bundle.score.internal_consistency = 'INCONSISTENT -- resolved variants were not used'; },
  ]) {
    const bundle = exampleBundle(); mutate(bundle);
    assert.equal(getDisplayResult(bundle).kind, 'incomplete');
    assert.equal(getDisplayResult(bundle).score, null);
  }
});

test('numeric strings, NaN and infinity never become result values', () => {
  for (const value of ['60', NaN, Infinity, -1, 101]) {
    const bundle = exampleBundle(); bundle.interpret.percentile = value;
    assert.equal(getDisplayResult(bundle).kind, 'incomplete');
  }
  const bundle = exampleBundle(); bundle.report.delivered.score = '0.25'; delete bundle.score;
  assert.ok(validateArtifactBundle(bundle).some(value => value.code === 'score_required'));
});

test('raw zero is retained as a real recorded value, not treated as absent', () => {
  const bundle = exampleBundle(); bundle.score.raw_score = 0; bundle.report.delivered.score = 0;
  assert.equal(getDisplayResult(bundle).score, 0);
});

test('partial reference metadata cannot silently authorize a percentile', () => {
  for (const key of ['source', 'version_string', 'cohort_construction_rule', 'assay_layer', 'scoring_parity', 'n_participants', 'distribution']) {
    const bundle = exampleBundle(); delete bundle.reference_response.reference[key];
    assert.equal(getDisplayResult(bundle).kind, 'incomplete', key);
  }
  const bundle = exampleBundle(); bundle.reference_response.reference.usable = false;
  assert.equal(getDisplayResult(bundle).kind, 'incomplete');
});

test('reference provenance conflicts and unfinished earlier stages suppress a final score', () => {
  const conflict = exampleBundle();
  conflict.interpret.reference = { source: 'A different example reference' };
  assert.ok(validateArtifactBundle(conflict).some(value => value.code === 'reference_mismatch'));
  assert.equal(getDisplayResult(conflict).kind, 'incomplete');
  for (const outcome of ['refuse', 'stop', 'ask', 'session-ended-by-person']) {
    const bundle = exampleBundle(); bundle.model_choice = exampleJudgment(outcome);
    assert.ok(validateArtifactBundle(bundle).some(value => value.code === 'unfinished_before_report'));
    assert.equal(getDisplayResult(bundle).score, null);
  }
});

test('a score needs a named model and cannot borrow a different model’s reference', () => {
  const missing = exampleBundle(); delete missing.score.model;
  assert.ok(validateArtifactBundle(missing).some(value => value.code === 'score_model_required'));
  assert.equal(getDisplayResult(missing).score, null);
  const conflict = exampleBundle();
  conflict.reference_response.request_echo = { model: { pgs_id: 'PGS000001' } };
  assert.ok(validateArtifactBundle(conflict).some(value => value.code === 'model_identity_mismatch'));
  assert.equal(getDisplayResult(conflict).score, null);
  conflict.reference_response.request_echo.model.pgs_id = 'PGS000000';
  assert.equal(getDisplayResult(conflict).model, 'PGS000000');
});

test('the reference request must agree with the recorded score and measured coverage', () => {
  for (const patient of [{ raw_score: 3 }, { resolved_weight_fraction: 0.1 }]) {
    const bundle = exampleBundle(); bundle.reference_response.request_echo = { patient };
    assert.ok(validateArtifactBundle(bundle).some(value => value.code === 'reference_request_mismatch'));
    assert.equal(getDisplayResult(bundle).score, null);
  }
});

test('event history cannot authorize a stale score while the run is asking, blocked or ended', () => {
  for (const event of [
    { kind: 'question', stage: 'select', question: 'Example question', question_id: 'example' },
    { kind: 'blocked', stage: 'interpret' },
    { kind: 'run_completed', outcome: 'refuse' },
    { kind: 'run_completed', outcome: 'stop' },
    { kind: 'run_failed' },
    { kind: 'session_end' },
  ]) {
    const bundle = exampleBundle(); bundle.events = [event];
    assert.ok(validateArtifactBundle(bundle).some(value => value.code === 'report_event_conflict'));
    assert.equal(getDisplayResult(bundle).score, null);
  }
  const resumed = exampleBundle(); resumed.events = [{ kind: 'blocked' }, { kind: 'run_completed', outcome: 'score' }];
  assert.equal(getDisplayResult(resumed).kind, 'score');
});

test('a refusal renders the direct envelope, without requiring scored artifacts', () => {
  const report = exampleRefusal();
  assert.deepEqual(validateReport(report), []);
  const display = getDisplayResult({ report });
  assert.equal(display.kind, 'refuse');
  assert.equal(display.envelope, report.refusal_envelope);
  assert.equal(display.score, null);
});

test('refusals with absent delivery or obvious log copy are held for correction', () => {
  const missing = exampleRefusal(); delete missing.refusal_envelope;
  assert.ok(validateReport(missing).some(value => value.code === 'envelope_required'));
  const logged = exampleRefusal(); logged.refusal_envelope.detail = 'REP-4: told them that your file was checked.';
  assert.ok(validateReport(logged).some(value => value.code === 'log_register'));
});

test('blocked provider transport, stop, ask and voluntary ending stay separate', () => {
  assert.equal(getDisplayResult({ reference_response: { status: 'reference_unavailable' } }).kind, 'blocked');
  assert.equal(getDisplayResult({ score: exampleJudgment('stop') }).kind, 'stop');
  assert.equal(getDisplayResult({ model_choice: exampleJudgment('ask') }).kind, 'ask');
  assert.equal(getDisplayResult({ ancestry: exampleJudgment('session-ended-by-person') }).kind, 'session-ended-by-person');
  assert.equal(getDisplayResult({ status: 'session-ended-by-person' }).kind, 'session-ended-by-person');
  assert.ok(validateReport(exampleJudgment('ask')).some(value => value.code === 'final_report_outcome'));
});

test('partial stage imports validate contracts without inventing missing stages', () => {
  assert.deepEqual(validateArtifactBundle({}), []);
  assert.equal(getDisplayResult({}).kind, 'pending');
  const bundle = {
    stages: { door: { ...exampleJudgment('proceed'), assay_class: 'genotyping-array', absence_means: 'Example: unmeasured.', confidence: 'Example assessment.', genome_build: { value: 'GRCh37', verified_by: ['Example marker cross-check.'] } } },
  };
  assert.deepEqual(validateArtifactBundle(bundle), []);
  delete bundle.stages.door.absence_means;
  assert.ok(validateArtifactBundle(bundle).some(value => value.path === 'classification.absence_means'));
});

test('ancestry disagreement cannot be silently hidden before selecting a model', () => {
  const bundle = { ancestry: { ...exampleJudgment('proceed'), inferred_group: 'Example genetic grouping', agreement: 'disagree', used_for_math: 'inferred (ANC-4)', surfaced_to_person: false } };
  assert.ok(validateArtifactBundle(bundle).some(value => value.code === 'mismatch_not_surfaced'));
  bundle.ancestry.surfaced_to_person = true;
  assert.deepEqual(validateArtifactBundle(bundle), []);
});

test('model recommendation requires a cost, a bare id and the correct gate', () => {
  const bundle = { model_choice: {
    ...exampleJudgment('proceed'), chosen_model: 'PGS000000', trait_id: 'EXAMPLE_0000', gate_type: 'transparency',
    candidates_considered: [{ pgs_id: 'PGS000000', why_ranked_here: 'Example ranking.', cost_of_choosing_it: 'Example evidence limitation.' }],
    coverage_estimate: { resolved_weight_fraction: 0.99, is_an_estimate: true },
  } };
  assert.deepEqual(validateArtifactBundle(bundle), []);
  delete bundle.model_choice.candidates_considered[0].cost_of_choosing_it;
  assert.ok(validateArtifactBundle(bundle).some(value => value.code === 'candidate_cost_required'));
  bundle.model_choice.chosen_model = 'PGS000001';
  assert.ok(validateArtifactBundle(bundle).some(value => value.code === 'chosen_candidate_missing'));
  delete bundle.model_choice.coverage_estimate;
  assert.ok(validateArtifactBundle(bundle).some(value => value.code === 'estimate_label_required'));
});

test('untrusted non-object artifacts return issues rather than throwing', () => {
  for (const value of [null, [], 'report']) {
    assert.ok(validateArtifactBundle(value).length);
    assert.equal(getDisplayResult(value).kind, 'incomplete');
    assert.ok(validateReport(value).length);
    assert.doesNotThrow(() => readArtifacts(value));
  }
});

test('malformed JSON fields cannot invoke coercion hooks or crash validation', () => {
  const poison = { toString: null, valueOf: null };
  const bundle = exampleBundle();
  bundle.score.model = poison;
  bundle.score.internal_consistency = poison;
  bundle.model_choice = { ...exampleJudgment('proceed'), chosen_model: poison, trait_id: poison, candidates_considered: poison };
  assert.doesNotThrow(() => validateArtifactBundle(bundle));
  assert.equal(getDisplayResult(bundle).kind, 'incomplete');
  assert.doesNotThrow(() => validateReport(exampleBundle().report, null));
});
