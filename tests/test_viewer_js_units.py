from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for viewer JS unit tests")
def test_viewer_split_js_core_units_run_without_playwright() -> None:
    script = textwrap.dedent(
        r"""
        const assert = require('assert/strict');
        const fs = require('fs');
        const path = require('path');
        const vm = require('vm');

        const repoRoot = process.argv.at(-1);
        const assetDir = path.join(repoRoot, 'claude_tap', 'viewer_assets');

        function classList() {
          return { add() {}, remove() {}, toggle() {}, contains() { return false; } };
        }

        function element() {
          return {
            style: {},
            dataset: {},
            classList: classList(),
            children: [],
            innerHTML: '',
            textContent: '',
            value: '',
            setAttribute() {},
            appendChild(child) { this.children.push(child); return child; },
            removeChild(child) { this.children = this.children.filter(item => item !== child); },
            addEventListener() {},
            querySelector() { return null; },
            querySelectorAll() { return []; },
            focus() {},
            select() {},
            setSelectionRange() {},
            remove() {},
          };
        }

        const context = {
          console,
          URLSearchParams,
          setTimeout() {},
          clearTimeout() {},
          requestAnimationFrame(callback) { if (typeof callback === 'function') callback(); return 1; },
          cancelAnimationFrame() {},
          window: {
            location: { search: '?embed=1&hideHeader=1&density=compact&theme=dark' },
            localStorage: { getItem() { return null; }, setItem() {} },
            matchMedia() { return { matches: false }; },
          },
          navigator: { language: 'en', clipboard: null },
          document: {
            documentElement: { dataset: {}, classList: classList() },
            body: element(),
            querySelector() { return element(); },
            querySelectorAll() { return []; },
            getElementById() { return element(); },
            createElement() { return element(); },
            addEventListener() {},
            removeEventListener() {},
            execCommand() { return false; },
          },
        };
        vm.createContext(context);

        for (const assetName of [
          'state.js',
          'responses.js',
          'filters_search.js',
          'renderers.js',
          'diff.js',
          'utilities_mobile.js',
        ]) {
          const source = fs.readFileSync(path.join(assetDir, assetName), 'utf8');
          vm.runInContext(source, context, { filename: assetName });
        }

        const plain = value => JSON.parse(JSON.stringify(value));

        assert.deepEqual(plain(context.parseEmbedQueryOptions()), {
          enabled: true,
          hideHeader: true,
          hidePath: false,
          hideHistory: false,
          hideControls: false,
          compact: true,
          theme: 'dark',
        });

        assert.deepEqual(plain(context.turnSortSegments('1.02.beta')), [1, 2, 0]);
        assert.equal(context.compareTurns('1.10', '1.2') > 0, true);
        assert.equal(context.compareTurns('2', '10') < 0, true);

        assert.deepEqual(
          plain(context.lineDiff('alpha\nold\nsame', 'alpha\nnew\nsame')),
          [
            { type: 'ctx', text: 'alpha' },
            { type: 'change', oldText: 'old', newText: 'new' },
            { type: 'ctx', text: 'same' },
          ],
        );

        const events = [
          { event: 'response.created', data: { response: { id: 'resp_first' } } },
          {
            event: 'response.output_item.done',
            data: {
              output_index: 0,
              item: {
                id: 'item_first_tool',
                type: 'function_call',
                call_id: 'call_1',
                name: 'shell',
                arguments: '{"cmd":"pwd"}',
              },
            },
          },
          {
            event: 'response.completed',
            data: { response: { id: 'resp_first', output: [], usage: { output_tokens: 1 } } },
          },
          { event: 'response.created', data: { response: { id: 'resp_prefetch', generate: false } } },
          {
            event: 'response.completed',
            data: { response: { id: 'resp_prefetch', generate: false, usage: { output_tokens: 0 } } },
          },
        ];
        const groups = context.splitWebSocketResponseEvents(events);
        assert.equal(groups.length, 2);
        assert.equal(context.completedResponseFromEvents(groups[0].events).id, 'resp_first');
        assert.deepEqual(
          plain(groups.filter(group => context.isDisplayableWebSocketResponseGroup(group)).map(group => group.responseId)),
          ['resp_first'],
        );
        assert.deepEqual(plain(context.webSocketOutputMessages(groups[0].events)), [
          {
            type: 'message',
            role: 'assistant',
            content: [{
              type: 'tool_use',
              id: 'call_1',
              name: 'shell',
              input: { cmd: 'pwd' },
            }],
          },
        ]);

        assert.deepEqual(plain(context.normalizeDisplayContentBlocks([
          { type: 'input_text', text: 'hello' },
          { type: 'input_image', source: { media_type: 'image/png', data: 'base64-data' } },
          { type: 'tool_result', tool_use_id: 'call_1', content: 'ok' },
        ])), [
          { type: 'input_text', text: 'hello' },
          { type: 'input_image', source: { media_type: 'image/png', data: 'base64-data' } },
          { type: 'tool_result', tool_use_id: 'call_1', content: 'ok' },
        ]);

        assert.deepEqual(plain(context.getMessages({
          instructions: 'Be concise',
          input: [{ role: 'user', content: [{ type: 'input_text', text: 'Hi' }] }],
        })), [
          { role: 'developer', content: [{ type: 'text', text: 'Be concise' }] },
          { role: 'user', content: [{ type: 'input_text', text: 'Hi' }] },
        ]);
        """
    )

    subprocess.run(["node", "-e", script, str(REPO_ROOT)], check=True, capture_output=True, text=True)
