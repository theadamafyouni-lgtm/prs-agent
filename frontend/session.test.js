import test from 'node:test';
import assert from 'node:assert/strict';
import { createSessionState, applySessionEvent } from './session.js';

const apply = (events, state = createSessionState()) => events.reduce(applySessionEvent, state);
const stage = (state, id) => state.stages.find((item) => item.id === id);

test('partial runs use declared stages and the door alias without invented progress or time', () => {
  const initial = createSessionState();
  const state = applySessionEvent(initial, {
    kind: 'run_started', stages: ['door', 'ancestry', 'select'],
  });
  assert.deepEqual(state.stages.map((item) => item.id), ['read-and-reason', 'ancestry', 'select']);
  assert.ok(state.stages.every((item) => item.status === 'pending' && item.attempt === 0));
  assert.equal(state.lastEventAt, null);
  assert.equal(initial.status, 'idle');
  assert.equal(initial.events.length, 0);
  assert.equal(initial.stages.length, 6);
});

test('questions carry context; only their matching answer resumes waiting for the agent', () => {
  const asked = apply([{
    kind: 'question', question_id: 'q1', stage: 'select',
    question: 'Example question', why_it_matters: 'Example reason', hard_blocker: true,
  }]);
  const unmatched = applySessionEvent(asked, {
    kind: 'answer', question_id: 'older-question', text: 'Example unrelated answer',
  });
  assert.equal(unmatched.pendingQuestion.question_id, 'q1');
  assert.equal(unmatched.status, 'ask');
  const answered = applySessionEvent(unmatched, {
    kind: 'answer', question_id: 'q1', text: "I don't know", skipped: true,
  });
  assert.equal(answered.pendingQuestion, null);
  assert.equal(answered.status, 'waiting-agent');
  assert.equal(stage(answered, 'select').status, 'waiting-agent');
  assert.equal(answered.messages[0].whyItMatters, 'Example reason');
  assert.equal(answered.messages[0].hardBlocker, true);
  assert.equal(answered.messages[2].text, "I don't know");
  assert.equal(answered.messages[2].skipped, true);
  assert.equal(asked.status, 'ask');
  assert.equal(asked.messages.length, 1);
});

test('select can be re-entered after scoring without erasing earlier attempts or raw outcomes', () => {
  const events = [
    { kind: 'stage_started', stage: 'select' },
    { kind: 'stage_completed', stage: 'select', outcome: 'proceed' },
    { kind: 'stage_started', stage: 'score' },
    { kind: 'stage_completed', stage: 'score', outcome: 'score' },
    { kind: 'stage_started', stage: 'select' },
  ];
  const firstPass = apply(events.slice(0, 2));
  const returned = apply(events.slice(2), firstPass);
  assert.equal(stage(firstPass, 'select').attempt, 1);
  assert.equal(stage(firstPass, 'select').outcome, 'proceed');
  assert.equal(stage(returned, 'select').attempt, 2);
  assert.equal(stage(returned, 'select').history.length, 3);
  assert.equal(stage(returned, 'select').history[1].outcome, 'proceed');
  assert.equal(returned.currentStage, 'select');
  assert.equal(returned.status, 'running');
  assert.equal(stage(firstPass, 'select').history.length, 2);
});

test('provider unavailable is blocked with no invented judgment and can explicitly resume', () => {
  const blocked = apply([
    { kind: 'stage_started', stage: 'interpret' },
    { kind: 'blocked', stage: 'interpret', reason: 'Example: provider unavailable' },
  ]);
  assert.equal(blocked.status, 'blocked');
  assert.equal(stage(blocked, 'interpret').outcome, null);
  assert.equal(stage(blocked, 'interpret').status, 'blocked');
  const resumed = applySessionEvent(blocked, { kind: 'stage_progress', stage: 'interpret' });
  assert.equal(resumed.status, 'running');
});

test('a question before the first ledger entry does not count as a separate attempt', () => {
  const state = apply([
    { kind: 'question', stage: 'select', question_id: 'q1', question: 'Example question' },
    { kind: 'answer', question_id: 'q1', text: 'Example answer' },
    { kind: 'stage_started', stage: 'select' },
  ]);
  assert.equal(stage(state, 'select').attempt, 1);
  assert.equal(state.status, 'running');
});

test('refusal, stop, operational failure and leaving are distinct', () => {
  assert.equal(apply([{ kind: 'refusal', stage: 'select', outcome: 'refuse' }]).status, 'refuse');
  assert.equal(apply([{ kind: 'refusal', stage: 'door', outcome: 'stop' }]).status, 'stop');
  assert.equal(apply([{ kind: 'run_failed', error: 'Example transport failure' }]).status, 'error');
  const left = apply([
    { kind: 'session_end', outcome: 'session-ended-by-person' },
    { kind: 'run_completed', outcome: 'stop' },
  ]);
  assert.equal(left.status, 'session-ended-by-person');
  assert.equal(left.events.length, 2);
});

test('only an explicit terminal score event makes a scored session', () => {
  const state = apply([{ kind: 'stage_completed', stage: 'score', outcome: 'score' }]);
  assert.equal(state.status, 'running');
  assert.equal(stage(state, 'score').status, 'complete');
  assert.equal(applySessionEvent(state, { kind: 'run_completed', outcome: 'score' }).status, 'score');
  assert.equal(applySessionEvent(state, { kind: 'run_completed' }).status, 'unknown');
});

test('event ids deduplicate replay, equal timestamps do not, and missing timestamps stay missing', () => {
  const first = { kind: 'stage_started', stage: 'door', event_id: 'e1', ts: 'example-timestamp' };
  const initial = apply([first]);
  assert.equal(applySessionEvent(initial, { ...first }), initial);
  const state = apply([
    { kind: 'stage_progress', stage: 'door', event_id: 'e2', ts: 'example-timestamp' },
    { kind: 'stage_progress', stage: 'door', event_id: 'e3' },
  ], initial);
  assert.equal(state.events.length, 3);
  assert.equal(state.lastEventAt, 'example-timestamp');
  assert.equal('ts' in state.events[2], false);
});

test('unknown events remain inspectable and cannot complete a run', () => {
  const state = apply([{ kind: 'future-event', detail: 'Example adapter addition' }]);
  assert.equal(state.status, 'idle');
  assert.equal(state.events.length, 1);
  assert.equal(applySessionEvent(state, null), state);
});
