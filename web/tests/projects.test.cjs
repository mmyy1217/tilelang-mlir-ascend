const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const ts = require('typescript');
for (const extension of ['.ts', '.tsx']) {
  require.extensions[extension] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React, esModuleInterop: true},
  }).outputText, filename);
}
const {forkExploration, newExploration} = require('../src/projects.ts');
const {parseSnapshot} = require('../src/components/WorkbenchMenu.tsx');
const {EXPLORATIONS} = require('../src/mockData.ts');

test('forks isolate mutable state, preserve evidence and detach live execution', () => {
  const source = structuredClone(EXPLORATIONS[0]);
  const before = JSON.stringify(source);
  const first = forkExploration(source, 'A');
  const second = forkExploration(source, 'B');
  assert.notEqual(first.id, second.id);
  assert.notEqual(first.id, source.id);
  assert.equal(first.forkSource.explorationId, source.id);
  assert.equal(first.forkSource.eventSeq, source.lastEventSeq ?? 0);
  assert.equal(first.coordinatorStatus, 'stopped');
  assert.equal(first.budget.reservedEvaluations, 0);
  assert.equal(first.budget.usedEvaluations, source.budget.usedEvaluations);
  assert.deepEqual(first.resources, source.resources);
  for (const node of first.nodes) {
    assert.equal(node.explorationId, first.id);
    const original = source.nodes.find(n => n.id === node.id);
    for (const [key, stage] of Object.entries(node.stages)) {
      stage.attempts.forEach((attempt, i) => {
        const previous = original.stages[key].attempts[i];
        if (['pending', 'running'].includes(previous.status)) {
          assert.equal(attempt.status, 'stopped');
          assert.equal(attempt.sourceStatus, previous.status);
        } else assert.deepEqual(attempt, previous);
        assert.equal(attempt.agentId, previous.agentId);
        assert.deepEqual(attempt.dependsOnAttemptIds, previous.dependsOnAttemptIds);
      });
    }
  }
  assert.ok(first.agents.every(agent => agent.historicalOnly && !agent.currentNodeId));
  first.nodes[0].title = 'changed';
  assert.notEqual(first.nodes[0].title, second.nodes[0].title);
  assert.equal(JSON.stringify(source), before);
});

test('empty projects and fork provenance survive export/import without invented nodes', () => {
  const empty = newExploration('empty');
  assert.equal(empty.nodes.length, 0);
  for (const snapshot of [empty, forkExploration(EXPLORATIONS[0], 'copy')]) {
    assert.deepEqual(parseSnapshot(JSON.stringify({format: 'workbench-snapshot', version: 1, exploration: snapshot})), snapshot);
  }
  assert.throws(() => parseSnapshot('{}'));
});

const {graphRelations, graphDepths} = require('../src/graph.ts');
test('provenance graph keeps multiple sources, knowledge evidence and fork relations', () => {
  const source = structuredClone(EXPLORATIONS[0]);
  const fusion = source.nodes.find(n => n.id === 'N4');
  fusion.parentIds = ['N1', 'N3'];
  const relations = graphRelations(source);
  assert.equal(relations.filter(r => r.kind === 'derivation' && r.targetId === 'N4').length, 2);
  assert.ok(relations.some(r => r.kind === 'support' && r.targetType === 'knowledge'));
  assert.ok(relations.some(r => r.kind === 'refute'));
  source.graphRelations = relations;
  assert.deepEqual(forkExploration(source, 'graph copy').graphRelations, relations);
  assert.deepEqual(parseSnapshot(JSON.stringify(source)).graphRelations, relations);
  source.graphRelations.push({id:'cycle',kind:'derivation',sourceType:'node',sourceId:'N4',targetType:'node',targetId:'N0',rationale:'invalid cycle'});
  assert.throws(() => graphDepths(source));
  assert.throws(() => parseSnapshot(JSON.stringify(source)));
});
