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
          'lazy_loading.js',
          'i18n_ui.js',
          'live_bootstrap.js',
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

        assert.deepEqual(
          plain(context.getRequestTools({
            model: 'gpt-5.6-sol',
            input: [{
              type: 'additional_tools',
              role: 'developer',
              tools: [
                { name: 'exec', description: 'Run a command' },
                { name: 'wait' },
                { name: 'request_user_input' },
              ],
            }],
          }).map(tool => context.toolDisplayName(tool))),
          ['exec', 'wait', 'request_user_input'],
        );

        assert.deepEqual(
          plain(context.getRequestTools({
            tools: [{ name: 'exec' }],
            input: [{
              type: 'additional_tools',
              tools: [{ name: 'exec' }, { name: 'collaboration' }],
            }],
          }).map(tool => context.toolDisplayName(tool))),
          ['exec', 'collaboration'],
        );

        const cursorStepOne = {
          transport: 'cursor-transcript',
          request: {
            method: 'CURSOR_TRANSCRIPT',
            path: '/cursor/transcript/abc/turn/1/step/1',
            body: { messages: [{ role: 'user', content: 'inspect files' }] },
          },
          response: {
            status: 200,
            body: {
              content: [
                { type: 'text', text: 'looking' },
                { type: 'tool_use', name: 'Glob', input: { glob_pattern: 'README*' } },
                { type: 'tool_use', name: 'Shell', input: { command: 'ls' } },
              ],
            },
          },
        };
        const cursorStepTwo = {
          transport: 'cursor-transcript',
          request: {
            method: 'CURSOR_TRANSCRIPT',
            path: '/cursor/transcript/abc/turn/1/step/2',
            body: { messages: [{ role: 'user', content: 'inspect files' }] },
          },
          response: {
            status: 200,
            body: {
              content: [
                { type: 'tool_use', name: 'Read', input: { path: 'README.md', limit: 80 } },
              ],
            },
          },
        };
        const otherCursorSession = {
          transport: 'cursor-transcript',
          request: {
            method: 'CURSOR_TRANSCRIPT',
            path: '/cursor/transcript/other/turn/1/step/1',
            body: { messages: [{ role: 'user', content: 'search the web' }] },
          },
          response: {
            status: 200,
            body: {
              content: [
                { type: 'tool_use', name: 'WebSearch', input: { search_term: 'claude-tap' } },
              ],
            },
          },
        };
        context.cursorStepOne = cursorStepOne;
        context.cursorStepTwo = cursorStepTwo;
        context.otherCursorSession = otherCursorSession;
        vm.runInContext('entries = [cursorStepOne, cursorStepTwo, otherCursorSession]', context);
        assert.deepEqual(
          plain(context.getDetailTools(cursorStepOne, cursorStepOne.request.body, cursorStepOne.response.body)
            .map(tool => [context.toolDisplayName(tool), Object.keys(tool.input_schema.properties)])),
          [['Glob', ['glob_pattern']], ['Shell', ['command']], ['Read', ['path', 'limit']]],
        );
        assert.deepEqual(
          plain(context.getDetailTools(otherCursorSession, otherCursorSession.request.body, otherCursorSession.response.body)
            .map(tool => context.toolDisplayName(tool))),
          ['WebSearch'],
        );
        assert.equal(context.cursorTranscriptConversationKey(cursorStepOne), 'abc');
        assert.equal(context.cursorTranscriptConversationKey(otherCursorSession), 'other');
        assert.equal(
          context.cursorTranscriptConversationKey({ capture: { cursor_transcript_id: 'captured-id' } }),
          'captured-id',
        );
        assert.equal(context.getRequestTools(cursorStepOne.request.body).length, 0);
        vm.runInContext('entries = []', context);

        const namespaceToolsHtml = context.renderTools([{
          type: 'namespace',
          name: 'functions',
          description: '',
          tools: [
            {
              type: 'function',
              name: 'exec',
              description: 'Run JavaScript orchestration.',
              parameters: {
                type: 'object',
                properties: { source: { type: 'string', description: 'JavaScript source.' } },
                required: ['source'],
              },
            },
            { type: 'function', name: 'wait', description: 'Wait for a yielded cell.' },
          ],
        }]);
        assert.match(namespaceToolsHtml, /tool-namespace/);
        assert.match(namespaceToolsHtml, /functions/);
        assert.match(namespaceToolsHtml, /2 badge_tools/);
        assert.match(namespaceToolsHtml, /tool-block-nested/);
        assert.match(namespaceToolsHtml, /exec/);
        assert.match(namespaceToolsHtml, /wait/);
        assert.match(namespaceToolsHtml, /JavaScript source\./);

        const codexPrefetchId = 'resp_prefetch_tools';
        const codexVisibleId = 'resp_visible';
        const codexExpanded = context.expandWebSocketResponseEntries([
          {
            transport: 'websocket',
            request: {
              method: 'WEBSOCKET',
              path: '/v1/responses',
              body: {
                model: 'gpt-5.6-sol',
                generate: false,
                input: [{
                  type: 'additional_tools',
                  role: 'developer',
                  tools: [
                    { name: 'exec' },
                    { name: 'wait' },
                    { name: 'request_user_input' },
                    { name: 'collaboration' },
                  ],
                }],
              },
            },
            response: {
              body: {
                id: codexPrefetchId,
                generate: false,
                output: [],
                usage: { input_tokens: 10, output_tokens: 0 },
              },
            },
          },
          {
            transport: 'websocket',
            request: {
              method: 'WEBSOCKET',
              path: '/v1/responses',
              body: {
                model: 'gpt-5.6-sol',
                previous_response_id: codexPrefetchId,
                input: [{ type: 'message', role: 'user', content: [{ type: 'input_text', text: 'Run pwd' }] }],
              },
            },
            response: {
              body: {
                id: codexVisibleId,
                previous_response_id: codexPrefetchId,
                output: [{ type: 'message', role: 'assistant', content: [{ type: 'output_text', text: 'ok' }] }],
                usage: { input_tokens: 20, output_tokens: 2 },
              },
            },
          },
        ]);
        assert.equal(codexExpanded.length, 1);
        assert.deepEqual(
          plain(context.getRequestTools(codexExpanded[0].request.body).map(tool => context.toolDisplayName(tool))),
          ['exec', 'wait', 'request_user_input', 'collaboration'],
        );
        assert.deepEqual(
          plain(context.getMessages(codexExpanded[0].request.body).map(message => message.role)),
          ['user'],
        );

        const compactBundle = {
          __claude_tap_compact_trace__: { version: 1 },
          blobs: {
            hash_1: {
              kind: 'json',
              payload: {
                method: 'POST',
                path: '/v1/responses',
                body: { input: [{ role: 'user', content: 'compact prompt' }] },
              },
            },
          },
          records: [{
            __claude_tap_compact_record__: {
              version: 1,
              refs: [{ path: '/request', hash: 'hash_1', bytes: 100 }],
            },
            record: {
              turn: 1,
              request: {
                __claude_tap_blob_ref__: { version: 1, kind: 'json', hash: 'hash_1' },
              },
              response: {
                status: 200,
                body: {
                  output: [{
                    type: 'message',
                    content: [{
                      type: 'output_text',
                      text: 'marker-shaped user payload',
                      metadata: {
                        __claude_tap_blob_ref__: {
                          version: 1,
                          kind: 'json',
                          hash: 'user-controlled-marker-shape',
                        },
                      },
                    }],
                  }],
                },
              },
            },
          }],
        };
        const fakeUserMarker = {
          __claude_tap_blob_ref__: {
            version: 1,
            kind: 'json',
            hash: 'user-controlled-marker-shape',
          },
        };
        assert.deepEqual(plain(context.materializeCompactTraceBundle(compactBundle)), [{
          turn: 1,
          request: {
            method: 'POST',
            path: '/v1/responses',
            body: { input: [{ role: 'user', content: 'compact prompt' }] },
          },
          response: {
            status: 200,
            body: {
              output: [{
                type: 'message',
                content: [{
                  type: 'output_text',
                  text: 'marker-shaped user payload',
                  metadata: fakeUserMarker,
                }],
              }],
            },
          },
        }]);
        assert.deepEqual(
          plain(context.parseTraceText(JSON.stringify(compactBundle))),
          plain(context.materializeCompactTraceBundle(compactBundle)),
        );

        const legacyCompactBundle = {
          __claude_tap_compact_trace__: { version: 1 },
          blobs: {
            hash_legacy_instructions: {
              kind: 'json',
              payload: 'legacy compact instructions',
            },
            hash_legacy_input: {
              kind: 'json',
              payload: {
                role: 'user',
                content: [{ type: 'input_text', text: 'legacy compact input item' }],
              },
            },
          },
          records: [{
            __claude_tap_compact_record__: {
              version: 1,
              encoding: 'json-blob-ref',
            },
            record: {
              turn: 2,
              request: {
                body: {
                  instructions: {
                    __claude_tap_blob_ref__: { version: 1, kind: 'json', hash: 'hash_legacy_instructions' },
                  },
                  input: [
                    {
                      __claude_tap_blob_ref__: { version: 1, kind: 'json', hash: 'hash_legacy_input' },
                    },
                    {
                      role: 'user',
                      content: [{ type: 'input_text', text: 'keep marker shape' }],
                      metadata: fakeUserMarker,
                    },
                  ],
                },
              },
              response: { body: { output: [] } },
            },
          }],
        };
        assert.deepEqual(plain(context.materializeCompactTraceBundle(legacyCompactBundle)), [{
          turn: 2,
          request: {
            body: {
              instructions: 'legacy compact instructions',
              input: [
                {
                  role: 'user',
                  content: [{ type: 'input_text', text: 'legacy compact input item' }],
                },
                {
                  role: 'user',
                  content: [{ type: 'input_text', text: 'keep marker shape' }],
                  metadata: fakeUserMarker,
                },
              ],
            },
          },
          response: { body: { output: [] } },
        }]);

        /* ── normalizeUsage: provider-aware cache flag ── */

        // OpenAI-style: cached_tokens embedded in prompt_tokens via details
        const openaiUsage = context.normalizeUsage({
          prompt_tokens: 100,
          completion_tokens: 50,
          prompt_tokens_details: { cached_tokens: 60 },
        });
        assert.equal(openaiUsage.input_tokens, 100);
        assert.equal(openaiUsage.cache_read_input_tokens, 60);
        assert.equal(openaiUsage._cache_read_in_input, true);

        // Claude/Anthropic-style: cache_read_input_tokens separate from input_tokens
        const claudeUsage = context.normalizeUsage({
          input_tokens: 40,
          output_tokens: 20,
          cache_read_input_tokens: 60,
          cache_creation_input_tokens: 10,
        });
        assert.equal(claudeUsage.input_tokens, 40);
        assert.equal(claudeUsage.cache_read_input_tokens, 60);
        assert.equal(claudeUsage._cache_read_in_input, false);

        // Bedrock Converse-style camelCase: cacheReadInputTokens is a separate bucket
        const bedrockUsage = context.normalizeUsage({
          inputTokens: 9,
          outputTokens: 1,
          cacheReadInputTokens: 12,
          cacheWriteInputTokens: 2,
        });
        assert.equal(bedrockUsage.input_tokens, 9);
        assert.equal(bedrockUsage.cache_read_input_tokens, 12);
        assert.equal(bedrockUsage.cache_creation_input_tokens, 2);
        assert.equal(bedrockUsage._cache_read_in_input, false);

        /* OpenAI Realtime spells the bucket singular. pricing.py already lists it
           among the Realtime modality buckets, so skipping it here left the whole
           prompt billed at the input rate with no cache read shown. */
        const realtimeUsage = context.normalizeUsage({
          input_tokens: 51000,
          output_tokens: 100,
          input_token_details: { cached_tokens: 50000 },
        });
        assert.equal(realtimeUsage.cache_read_input_tokens, 50000,
          'a Realtime cache hit must be read out of the singular details bucket');
        assert.equal(realtimeUsage._cache_read_in_input, true);

        // No cache data at all: flag should be absent
        const noCacheUsage = context.normalizeUsage({ input_tokens: 100, output_tokens: 50 });
        assert.equal(noCacheUsage.cache_read_input_tokens, undefined);
        assert.equal(noCacheUsage._cache_read_in_input, undefined);

        /* ── Cache hit rate denominator correctness ── */

        // Simulate OpenAI-style: cache embedded in input → rate = 60/100 = 60%
        //   denominator = input_tokens = 100
        const openaiRate = Math.round(60 / 100 * 100);
        assert.equal(openaiRate, 60);

        // Simulate Claude-style: cache separate → total input-side = 40+60+10 = 110
        //   rate = 60/110 = 55% (NOT 60/40 = 150% which is the old buggy result)
        const claudeTotalInput = 40 + 60 + 10;
        const claudeRate = Math.round(60 / claudeTotalInput * 100);
        assert.equal(claudeRate, 55);
        assert.ok(claudeRate <= 100, 'Claude-style rate must not exceed 100%');

        /* ── Direct DOM test: #stat-cache-hit-rate via applyFilter() ── */

        context.assert = assert;
        context.element = element;

        vm.runInContext(`
          // Persistent stat elements so applyFilter can set textContent
          const _statEls = {};
          document.querySelector = function (sel) {
            if (typeof sel === 'string' && sel.startsWith('#')) {
              const id = sel.slice(1);
              if (!_statEls[id]) _statEls[id] = element();
              return _statEls[id];
            }
            return element();
          };
          // Stub heavy rendering helpers irrelevant to stat computation
          renderSidebar = function () {};
          updatePositionIndicator = function () {};
          renderToolFilter = function () {};
          renderPathFilter = function () {};
          renderTracePathBar = function () {};

          function makeUsageEntry(usage, path) {
            return {
              request: { path: path || '/v1/messages', method: 'POST', body: {} },
              response: { body: { usage } },
              turn: '1',
              duration_ms: 100,
            };
          }

          // Claude-style: cache_read separate from input → 60/(40+60+10)=55%
          entries = [makeUsageEntry({
            input_tokens: 40, output_tokens: 20,
            cache_read_input_tokens: 60, cache_creation_input_tokens: 10,
          })];
          activePaths = new Set(['/v1/messages']);
          searchQuery = '';
          activeTools = null;
          applyFilter();
          assert.equal(_statEls['stat-cache-hit-rate'].textContent, '55%',
            'Claude-style direct DOM: expected 55%');
          assert.equal(_statEls['stat-cache-hit-rate-group'].style.display, 'flex',
            'Claude-style direct DOM: group should be visible');

          // OpenAI-style: cache embedded in input → 60/100=60%
          entries = [makeUsageEntry({
            prompt_tokens: 100, completion_tokens: 50,
            prompt_tokens_details: { cached_tokens: 60 },
          })];
          applyFilter();
          assert.equal(_statEls['stat-cache-hit-rate'].textContent, '60%',
            'OpenAI-style direct DOM: expected 60%');

          // Bedrock camelCase: cache_read separate from input → 12/(9+12+2)=52%
          entries = [makeUsageEntry({
            inputTokens: 9, outputTokens: 1,
            cacheReadInputTokens: 12, cacheWriteInputTokens: 2,
          })];
          applyFilter();
          assert.equal(_statEls['stat-cache-hit-rate'].textContent, '52%',
            'Bedrock camelCase direct DOM: expected 52%');

          // No cache data: group should be hidden
          entries = [makeUsageEntry({ input_tokens: 100, output_tokens: 50 })];
          applyFilter();
          assert.equal(_statEls['stat-cache-hit-rate-group'].style.display, 'none',
            'No-cache direct DOM: group should be hidden');

          // Mixed providers: OpenAI(100,cache=60) + Claude(40,cache_read=60,create=10)
          // denom = 100 + 110 = 210, cache_read = 120, rate = 57%
          entries = [
            makeUsageEntry({
              prompt_tokens: 100, completion_tokens: 50,
              prompt_tokens_details: { cached_tokens: 60 },
            }),
            makeUsageEntry({
              input_tokens: 40, output_tokens: 20,
              cache_read_input_tokens: 60, cache_creation_input_tokens: 10,
            }),
          ];
          applyFilter();
          assert.equal(_statEls['stat-cache-hit-rate'].textContent, '57%',
            'Mixed-provider direct DOM: expected 57%');

          // Mixed cached and uncached entries: uncached input still belongs in denominator
          // denom = OpenAI input 100 + uncached input 100, cache_read = 60, rate = 30%
          entries = [
            makeUsageEntry({
              prompt_tokens: 100, completion_tokens: 50,
              prompt_tokens_details: { cached_tokens: 60 },
            }),
            makeUsageEntry({ input_tokens: 100, output_tokens: 10 }),
          ];
          applyFilter();
          assert.equal(_statEls['stat-cache-hit-rate'].textContent, '30%',
            'Mixed cached/uncached direct DOM: expected 30%');

          /* ── Cost stats: display and summation only ── */

          function makeCostEntry(requestId, cost, saved) {
            const entry = makeUsageEntry({ input_tokens: 100, output_tokens: 10 });
            entry.request_id = requestId;
            if (cost !== undefined) { entry.cost = cost; entry.saved = saved; }
            return entry;
          }

          /* Sub-cent totals must not collapse to $0.00 and report a paid trace
             as free. */
          assert.equal(formatCostUsd(0.0004), '$0.0004');
          assert.equal(formatCostUsd(1.5), '$1.50');
          assert.equal(formatCostUsd(-0.25), '-$0.25');
          assert.equal(formatCostUsd(0), '$0.00');
          assert.equal(formatCostUsd('nope'), '');
          assert.equal(formatCostUsd(Infinity), '');
          /* 100 uncached deepseek/deepseek-chat tokens are $0.000028; toFixed(4)
             would render that paid turn as $0.0000. */
          assert.equal(formatCostUsd(0.000028), '$0.000028');
          assert.equal(formatCostUsd(100 * 1.4e-7), '$0.000014');

          /* Drag-and-drop traces never reach Python, so nothing is priced. */
          EMBEDDED_COST_INDEX = undefined;
          EMBEDDED_PRICING_META = undefined;
          entries = [makeCostEntry('req_a')];
          applyFilter();
          assert.equal(entryCost(entries[0]), null, 'unpriced entry must yield null');
          assert.equal(pricingMeta(), null, 'no provenance without EMBEDDED_PRICING_META');
          assert.equal(_statEls['stat-cost-group'].style.display, 'none',
            'cost group hidden when nothing is priced');
          assert.equal(_statEls['stat-saved-group'].style.display, 'none',
            'saved group hidden when nothing is priced');

          /* Lazy mode: cost rides on the entry itself. */
          EMBEDDED_PRICING_META = { source: 'litellm', as_of: '2026-08-19' };
          entries = [makeCostEntry('req_a', 0.5, 0.25), makeCostEntry('req_b', 0.25, 0.125)];
          applyFilter();
          assert.equal(_statEls['stat-cost'].textContent, '$0.75');
          assert.equal(_statEls['stat-saved'].textContent, '$0.38');
          assert.equal(_statEls['stat-cost-group'].style.display, 'flex');
          assert.ok(_statEls['stat-cost-group'].title.indexOf('litellm') >= 0,
            'price source must be disclosed');
          assert.ok(_statEls['stat-cost-group'].title.indexOf('2026-08-19') >= 0,
            'price table date must be disclosed');

          /* Records mode: cost comes from the index, keyed by request id. */
          entries = [makeCostEntry('req_a'), makeCostEntry('req_b')];
          EMBEDDED_COST_INDEX = { req_a: { cost: 0.02, saved: 0.01 }, req_b: { cost: 0.03, saved: 0.02 } };
          applyFilter();
          assert.equal(_statEls['stat-cost'].textContent, '$0.05');
          assert.equal(_statEls['stat-saved'].textContent, '$0.03');

          /* A partially priced set must say so on the face of the stat, not
             only in a tooltip. */
          entries = [makeCostEntry('req_a'), makeCostEntry('req_missing')];
          applyFilter();
          assert.equal(_statEls['stat-cost'].textContent, '$0.02+',
            'partial totals are marked with a trailing +');
          assert.ok(_statEls['stat-cost-group'].title.indexOf('1') >= 0,
            'tooltip must count the excluded turns');

          /* Signed per-entry savings: a write-only turn costs more than an
             uncached one, but the displayed total is clamped at zero. */
          EMBEDDED_COST_INDEX = { req_a: { cost: 0.02, saved: -0.01 } };
          entries = [makeCostEntry('req_a')];
          applyFilter();
          assert.equal(entryCost(entries[0]).saved, -0.01, 'per-entry saved stays signed');
          assert.equal(_statEls['stat-saved'].textContent, '$0.00',
            'aggregate saved is clamped for display');

          /* Cost shows even when the token stats are hidden for lack of usage. */
          EMBEDDED_COST_INDEX = { req_nousage: { cost: 0.42, saved: 0.1 } };
          entries = [{
            request: { path: '/v1/messages', method: 'POST', body: {} },
            response: { body: {} },
            request_id: 'req_nousage',
            turn: '1',
            duration_ms: 100,
          }];
          applyFilter();
          assert.equal(_statEls['stat-input-group'].style.display, 'none',
            'token breakdown hidden without usage');
          assert.equal(_statEls['stat-cost'].textContent, '$0.42',
            'cost must not be gated on token totals');

          /* Live mode and the records API serve raw records with no generated
             index, so Python attaches the costs to the record itself. */
          EMBEDDED_COST_INDEX = {};
          const liveEntry = makeCostEntry('req_live');
          liveEntry._cost_index = { req_live: { cost: 0.11, saved: 0.05 } };
          entries = [liveEntry];
          applyFilter();
          assert.equal(entryCost(entries[0]).cost, 0.11, 'record-borne cost must be read');
          assert.equal(_statEls['stat-cost'].textContent, '$0.11');

          /* A WebSocket record split into several entries carries one keyed set
             per derived entry. */
          const wsFirst = makeCostEntry('req_ws:1');
          const wsSecond = makeCostEntry('req_ws:2');
          const wsIndex = { 'req_ws:1': { cost: 0.01, saved: 0 }, 'req_ws:2': { cost: 0.02, saved: 0 } };
          wsFirst._cost_index = wsIndex;
          wsSecond._cost_index = wsIndex;
          entries = [wsFirst, wsSecond];
          applyFilter();
          assert.equal(_statEls['stat-cost'].textContent, '$0.03');

          /* The upstream price file changes several times a day, so the tooltip
             names the commit as well as the date. */
          EMBEDDED_PRICING_META = { source: 'litellm', as_of: '2026-08-19', upstream_commit: '24fc3f721c4086a0ab8318f15a005309a8a55512' };
          entries = [makeCostEntry('req_a', 0.5, 0.25)];
          applyFilter();
          assert.ok(_statEls['stat-cost-group'].title.indexOf('24fc3f721c40') >= 0,
            'price snapshot commit must be disclosed');

          /* Gemini keeps reasoning tokens out of candidatesTokenCount but bills
             them at the output rate. */
          assert.equal(
            normalizeUsage({ promptTokenCount: 1000, candidatesTokenCount: 200, thoughtsTokenCount: 800 }).output_tokens,
            1000,
            'thinking tokens belong in the billed output total');

          /* Lazy stubs must keep the cost Python put on the metadata record. */
          const stub = buildStubEntry({
            request_id: 'req_stub',
            path: '/v1/messages',
            method: 'POST',
            cost: 0.07,
            uncached_cost: 0.2,
            saved: 0.13,
            priced_model: 'claude-sonnet-4-20250514',
            long_context: false,
          }, 0);
          assert.equal(stub.cost, 0.07, 'stub must carry cost');
          assert.equal(stub.saved, 0.13, 'stub must carry savings');
          assert.equal(stub.priced_model, 'claude-sonnet-4-20250514');

          /* Cache provenance travels on the metadata; re-inferring it from the
             model name misread every embedded-cache turn, because metadata
             carries cache_creation_input_tokens even when it is zero. */
          const embeddedStub = buildStubEntry({
            request_id: 'req_embedded',
            path: '/v1/chat/completions',
            method: 'POST',
            model: 'gpt-4o',
            input_tokens: 51000,
            output_tokens: 100,
            cache_read_input_tokens: 50000,
            cache_creation_input_tokens: 0,
            cache_read_in_input: true,
          }, 0);
          assert.equal(getUsage(embeddedStub)._cache_read_in_input, true,
            'an embedded-cache turn must not be counted against a doubled denominator');

          const separateStub = buildStubEntry({
            request_id: 'req_separate',
            path: '/v1/messages',
            method: 'POST',
            model: 'claude-sonnet-4-20250514',
            input_tokens: 120,
            cache_read_input_tokens: 40000,
            cache_creation_input_tokens: 2000,
            cache_read_in_input: false,
          }, 0);
          assert.equal(getUsage(separateStub)._cache_read_in_input, false,
            'a separate-bucket turn keeps its own denominator');

          /* A turn that reported no tokens has no unknown price to report. The
             full record has no usage object at all, so the stub must not invent
             an empty one -- it is truthy, and lazy mode alone would count every
             count_tokens call as a turn whose price could not be determined. */
          const tokenCountStub = buildStubEntry({
            request_id: 'req_count',
            path: '/v1/messages/count_tokens',
            method: 'POST',
            model: 'claude-sonnet-4-20250514',
          }, 0);
          assert.equal(getUsage(tokenCountStub), null,
            'a usage-free turn must not read as usage-bearing');

          /* Metadata written before the flag existed still gets the name check. */
          const legacyStub = buildStubEntry({
            request_id: 'req_legacy',
            path: '/v1/chat/completions',
            method: 'POST',
            model: 'gpt-4o',
            input_tokens: 51000,
            cache_read_input_tokens: 50000,
            cache_creation_input_tokens: 0,
          }, 0);
          assert.equal(getUsage(legacyStub)._cache_read_in_input, true,
            'a flag-less OpenAI stub still reads as embedded');

          /* ── Subscription turns: absent cost with a stated reason ── */

          /* A ChatGPT-subscription turn is not a priced turn and not an
             unknown-price turn either, so it must not land in the "no known
             price" count. */
          const subStub = buildStubEntry({
            request_id: 'req_sub',
            path: '/backend-api/codex/responses',
            method: 'POST',
            subscription: true,
          }, 0);
          assert.equal(subStub.subscription, true, 'stub must carry the subscription flag');
          assert.equal(subStub.cost, undefined, 'a subscription stub carries no cost');

          EMBEDDED_COST_INDEX = {};
          assert.equal(isSubscriptionEntry(subStub), true, 'stub flag must be recognised');
          assert.equal(entryCost(subStub), null, 'a subscription turn has no priced cost');
          assert.deepEqual(entryCostRecord(subStub), { subscription: true });

          /* Live mode and the records API carry the flag on the record instead. */
          const subLive = makeCostEntry('req_sub_live');
          subLive._cost_index = { req_sub_live: { subscription: true } };
          assert.equal(isSubscriptionEntry(subLive), true, 'record-borne flag must be recognised');
          assert.equal(entryCost(subLive), null);

          /* Records mode reads it from the generated index. */
          const subIndexed = makeCostEntry('req_sub_idx');
          EMBEDDED_COST_INDEX = { req_sub_idx: { subscription: true } };
          assert.equal(isSubscriptionEntry(subIndexed), true, 'indexed flag must be recognised');
          assert.equal(entryCost(subIndexed), null);

          assert.equal(isSubscriptionEntry(makeCostEntry('req_plain', 0.5, 0.25)), false,
            'a priced turn is not a subscription turn');
          assert.equal(isSubscriptionEntry(null), false);
          assert.equal(entryCostRecord(null), null);

          /* Subscription turns are counted apart from unpriceable ones, and the
             total is still marked partial because they are missing from it. */
          EMBEDDED_COST_INDEX = { req_a: { cost: 0.02, saved: 0.01 }, req_sub_idx: { subscription: true } };
          entries = [makeCostEntry('req_a'), makeCostEntry('req_sub_idx')];
          applyFilter();
          assert.equal(_statEls['stat-cost'].textContent, '$0.02+',
            'a subscription turn keeps the total marked partial');
          const subTitle = _statEls['stat-cost-group'].title;
          assert.ok(subTitle.indexOf('subscription') >= 0 || subTitle.indexOf('ChatGPT') >= 0,
            'tooltip must name the subscription reason');
        `, context);
        """
    )

    subprocess.run(["node", "-e", script, str(REPO_ROOT)], check=True, capture_output=True, text=True)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required for viewer JS unit tests")
def test_viewer_cache_invalidation_diagnostics_units() -> None:
    """The cache diagnosis must only claim a cause the captured payloads support.

    Real Claude Code traffic writes a small delta to the cache on nearly every
    turn, so cache_creation > 0 alone is not evidence of invalidation: only a
    write with no accompanying read means the prefix was genuinely cold.  Once a
    turn does qualify, Anthropic hashes the prompt as an ordered chain — tools,
    then system, then messages — so the earliest changed segment inside the
    cached region is the cause, and an edit beyond the last breakpoint is not a
    cause at all.

    The card must also decline to name a cause it cannot establish: an edit found
    after the cache lifetime already elapsed competes with expiry, expiry itself
    needs a predecessor confirmed by positive evidence, and a capture whose
    breakpoint position was stripped knows caching happened without knowing which
    segment it covered.
    """
    script = textwrap.dedent(
        r"""
        const assert = require('assert/strict');
        const fs = require('fs');
        const path = require('path');
        const vm = require('vm');

        const repoRoot = process.argv.at(-1);
        const assetDir = path.join(repoRoot, 'claude_tap', 'viewer_assets');
        const i18n = JSON.parse(fs.readFileSync(path.join(repoRoot, 'claude_tap', 'viewer_i18n.json'), 'utf8'));

        function classList() {
          return { add() {}, remove() {}, toggle() {}, contains() { return false; } };
        }
        function element() {
          return {
            style: {}, dataset: {}, classList: classList(), children: [],
            innerHTML: '', textContent: '', value: '',
            setAttribute() {}, appendChild(c) { this.children.push(c); return c; },
            addEventListener() {}, querySelector() { return null; },
            querySelectorAll() { return []; }, focus() {}, remove() {},
          };
        }

        const context = {
          console, URLSearchParams,
          setTimeout() {}, clearTimeout() {},
          requestAnimationFrame(cb) { if (typeof cb === 'function') cb(); return 1; },
          cancelAnimationFrame() {},
          __CLAUDE_TAP_I18N__: i18n,
          window: {
            location: { search: '' },
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
            addEventListener() {}, removeEventListener() {}, execCommand() { return false; },
          },
        };
        vm.createContext(context);

        for (const assetName of [
          'state.js', 'responses.js', 'lazy_loading.js', 'i18n_ui.js',
          'live_bootstrap.js', 'filters_search.js', 'renderers.js', 'diff.js',
          'utilities_mobile.js',
        ]) {
          vm.runInContext(fs.readFileSync(path.join(assetDir, assetName), 'utf8'), context, { filename: assetName });
        }

        const diagnose = context.diagnoseCacheInvalidation;
        const findPredecessor = context.findCachePredecessor;
        const isExactPredecessor = context.cachePredecessorIsExact;
        // `entries` is a top-level `let`, which lives in the shared script scope
        // rather than on the context object, so it is only reachable from inside
        // the vm.
        const loadEntries = vm.runInContext('(list) => { entries = list; filtered = list.slice(); }', context);
        const setFiltered = vm.runInContext('(list) => { filtered = list; }', context);

        const SYS = 'You are a helpful assistant.';
        const TOOLS = [{ name: 'Read', input_schema: { type: 'object', properties: { path: { type: 'string' } } } }];
        const SESSION = 'sess-unit';

        /* Build a turn shaped like a real Claude Code request: the system prompt
           carries the cache_control breakpoint that tells the viewer which
           segments are cached, and the session header scopes the cache to one
           conversation. */
        function turn(opts) {
          const control = { type: 'ephemeral' };
          if (opts.ttl) control.ttl = opts.ttl;
          const systemText = opts.system === undefined ? SYS : opts.system;
          return {
            request_id: opts.id,
            timestamp: opts.ts,
            request: {
              method: 'POST',
              path: '/v1/messages',
              headers: { 'X-Claude-Code-Session-Id': opts.session === undefined ? SESSION : opts.session },
              body: {
                model: opts.model || 'claude-opus-5',
                // `noBreakpoint` models a capture whose cache_control was stripped.
                system: opts.noBreakpoint ? systemText : [{ type: 'text', text: systemText, cache_control: control }],
                tools: opts.tools === undefined ? TOOLS : opts.tools,
                messages: opts.messages || [{ role: 'user', content: 'hello' }],
              },
            },
            response: { body: { usage: opts.usage } },
          };
        }

        /* A message whose own content block is a breakpoint, so the cached
           prefix reaches into the message list. */
        function cachedMsg(role, text) {
          return { role, content: [{ type: 'text', text, cache_control: { type: 'ephemeral' } }] };
        }

        const COLD = { input_tokens: 20, output_tokens: 30, cache_creation_input_tokens: 35580, cache_read_input_tokens: 0 };
        const WARM = { input_tokens: 9, output_tokens: 55, cache_creation_input_tokens: 718, cache_read_input_tokens: 34905 };
        const SEED = { input_tokens: 12, output_tokens: 40, cache_creation_input_tokens: 35115, cache_read_input_tokens: 0 };

        /* ── Healthy incremental extension: write AND read → no card ── */
        // Measured on a real claude-cli/2.1.233 session: 57 of 64 cache-bearing
        // turns look like this (e.g. create=718 alongside read=34905).
        const seed = turn({ id: 'r1', ts: '2026-08-14T10:00:00Z', usage: SEED });
        const extended = turn({
          id: 'r2', ts: '2026-08-14T10:00:30Z',
          messages: [{ role: 'user', content: 'hello' }, { role: 'assistant', content: 'hi' }],
          usage: WARM,
        });
        assert.equal(diagnose(extended, seed, true), null,
          'a write alongside a read is incremental extension, not invalidation');

        /* ── Nothing earlier left a cache behind → this turn created it ── */
        assert.equal(diagnose(seed, null, false).reasonKey, 'cache_miss_initial');
        assert.equal(diagnose(seed, null, false).lowConfidence, false,
          'the absence of a predecessor is itself conclusive');

        /* ── Prompt-hash order: tools, then system, then messages ── */
        // A schema edit that keeps every tool name still invalidates the cache,
        // so tools are compared by full definition rather than by name.
        const schemaEdited = turn({
          id: 'r3', ts: '2026-08-14T10:00:40Z',
          tools: [{ name: 'Read', input_schema: { type: 'object', properties: { path: { type: 'string' }, limit: { type: 'number' } } } }],
          usage: COLD,
        });
        assert.equal(diagnose(schemaEdited, seed, true).reasonKey, 'cache_miss_tools',
          'a tool schema edit that preserves every name still breaks the cache');

        // A changed system prompt inside the cache lifetime: the edit is the cause.
        const sysChanged = turn({ id: 'r4', ts: '2026-08-14T10:02:00Z', system: SYS + ' Be terse.', usage: COLD });
        assert.equal(diagnose(sysChanged, seed, true).reasonKey, 'cache_miss_system',
          'a system edit within the cache lifetime is the cause');

        // Both sides also declare a tool checkpoint that did not change. A cold
        // write then cannot be blamed on the later system edit.
        const toolBp = {
          name: 'Read',
          input_schema: { type: 'object', properties: { path: { type: 'string' } } },
          cache_control: { type: 'ephemeral' },
        };
        const dualPrev = turn({ id: 'r4c', ts: '2026-08-14T10:00:00Z', tools: [toolBp], usage: SEED });
        const dualSysChanged = turn({
          id: 'r4d', ts: '2026-08-14T10:02:00Z', tools: [toolBp],
          system: SYS + ' Be terse.', usage: COLD,
        });
        assert.equal(diagnose(dualSysChanged, dualPrev, true).reasonKey, 'cache_miss_unknown',
          'an unchanged earlier tool checkpoint should have produced cache reads');

        // The same edit 6.5 minutes later, past a 5-minute lifetime: the entry had
        // already expired, so the edit cannot be blamed for the cold write even
        // though the payloads differ.
        const sysChangedLate = turn({ id: 'r4b', ts: '2026-08-14T10:06:30Z', system: SYS + ' Be terse.', usage: COLD });
        assert.equal(diagnose(sysChangedLate, seed, true).reasonKey, 'cache_miss_ttl',
          'expiry outranks an edit made after the lifetime elapsed');

        // The cached prefix reaches into the messages on both sides.  A later
        // system checkpoint that stayed unchanged should have produced reads,
        // so a cold write cannot be blamed on the message edit.
        const histPrev = turn({ id: 'r5', ts: '2026-08-14T10:00:00Z', messages: [cachedMsg('user', 'hello')], usage: SEED });
        const histCur = turn({
          id: 'r6', ts: '2026-08-14T10:00:50Z',
          messages: [cachedMsg('user', 'totally different opening')], usage: COLD,
        });
        assert.equal(diagnose(histCur, histPrev, true).reasonKey, 'cache_miss_unknown',
          'an unchanged earlier system checkpoint should have produced cache reads');

        // With no earlier declared checkpoint, a message-scope edit is the cause.
        const histOnlyPrev = turn({
          id: 'r5b', ts: '2026-08-14T10:00:00Z', noBreakpoint: true,
          messages: [cachedMsg('user', 'hello')], usage: SEED,
        });
        const histOnlyCur = turn({
          id: 'r6b', ts: '2026-08-14T10:00:50Z', noBreakpoint: true,
          messages: [cachedMsg('user', 'totally different opening')], usage: COLD,
        });
        assert.equal(diagnose(histOnlyCur, histOnlyPrev, true).reasonKey, 'cache_miss_history');

        // Same edit, but no breakpoint reaches the messages: nothing in the
        // cached region changed, so blaming the history would point the reader at
        // the wrong segment.
        const tailPrev = turn({ id: 'r7', ts: '2026-08-14T10:00:00Z', messages: [{ role: 'user', content: 'hello' }], usage: SEED });
        const tailCur = turn({
          id: 'r8', ts: '2026-08-14T10:00:30Z',
          messages: [{ role: 'user', content: 'totally different opening' }], usage: COLD,
        });
        assert.equal(diagnose(tailCur, tailPrev, true).reasonKey, 'cache_miss_unknown',
          'an edit outside the cached region cannot be reported as an invalidating change');

        /* ── Idle expiry only once the prompt is known to be unchanged ── */
        const idle = turn({ id: 'r9', ts: '2026-08-14T10:07:00Z', usage: COLD });
        const ttlDiag = diagnose(idle, seed, true);
        assert.equal(ttlDiag.reasonKey, 'cache_miss_ttl');
        assert.equal(ttlDiag.lowConfidence, false, 'a confirmed predecessor supports a confident expiry claim');
        assert.ok(ttlDiag.reasonText.includes('5'), 'an ephemeral breakpoint with no ttl is the 5-minute tier');
        assert.ok(!ttlDiag.reasonText.includes('{minutes}'), 'the placeholder must be substituted');

        // An adjacency-only predecessor belongs to its own cache chain, so its
        // timestamp and TTL describe that chain: expiry cannot be derived from it.
        const adjacentOnly = diagnose(idle, seed, false);
        assert.equal(adjacentOnly.reasonKey, 'cache_miss_unknown',
          'expiry needs a predecessor confirmed by a link, session, or prefix');
        assert.equal(adjacentOnly.lowConfidence, true);

        // A 1-hour tier declared on the request must not be judged expired at
        // 7 minutes.
        const longTtlReq = turn({ id: 'r10', ts: '2026-08-14T10:00:00Z', ttl: '1h', usage: SEED });
        assert.equal(diagnose(idle, longTtlReq, true).reasonKey, 'cache_miss_unknown',
          'a 1h request-declared tier is still live after 7 minutes');

        // Anthropic also echoes the billed tier on the response; it wins when present.
        const longTtlResp = turn({
          id: 'r11', ts: '2026-08-14T10:00:00Z',
          usage: { ...SEED, cache_creation: { ephemeral_1h_input_tokens: 35115, ephemeral_5m_input_tokens: 0 } },
        });
        assert.equal(diagnose(idle, longTtlResp, true).reasonKey, 'cache_miss_unknown',
          'the response-side tier outranks the request default');

        // No tier from either source: the lifetime is unknown, so no elapsed
        // gap proves expiry no matter how long it is.
        const untieredPrev = turn({ id: 'r12', ts: '2026-08-14T10:00:00Z', noBreakpoint: true, usage: SEED });
        const halfHourLater = turn({ id: 'r13', ts: '2026-08-14T10:30:00Z', noBreakpoint: true, usage: COLD });
        const untiered = diagnose(halfHourLater, untieredPrev, true);
        assert.equal(untiered.reasonKey, 'cache_miss_unknown',
          'an unknown cache lifetime cannot support an expiry claim at any gap');
        assert.equal(untiered.lowConfidence, true);

        /* ── A stripped cache_control hides the breakpoint *position* ── */
        // Caching demonstrably happened, but not which segment it covered.  If the
        // real breakpoint sat on a tool, the system prompt followed it and was
        // never cached, so a system edit must not be named as the cause.
        const strippedPrev = turn({ id: 'r21', ts: '2026-08-14T10:00:00Z', noBreakpoint: true, usage: SEED });
        const strippedSysEdit = turn({
          id: 'r22', ts: '2026-08-14T10:00:30Z', noBreakpoint: true,
          system: SYS + ' Be terse.', usage: COLD,
        });
        assert.equal(diagnose(strippedSysEdit, strippedPrev, true).reasonKey, 'cache_miss_unknown',
          'an unknown breakpoint position cannot support a structural verdict');

        /* ── Only the cached prefix of each segment is compared ── */
        // The breakpoint sits on the first of two tools, so the second is outside
        // the cached region and editing it cannot have broken the cache.
        function twoToolTurn(opts) {
          const t2 = { name: 'Write', input_schema: { type: 'object', properties: { text: { type: opts.type } } } };
          const t1 = { name: 'Read', input_schema: { type: 'object', properties: { path: { type: 'string' } } },
            cache_control: { type: 'ephemeral' } };
          return turn({ ...opts, noBreakpoint: true, system: SYS, tools: [t1, t2] });
        }
        const toolPrefixPrev = twoToolTurn({ id: 'r23', ts: '2026-08-14T10:00:00Z', type: 'string', usage: SEED });
        const uncachedToolEdit = twoToolTurn({ id: 'r24', ts: '2026-08-14T10:00:30Z', type: 'number', usage: COLD });
        assert.equal(diagnose(uncachedToolEdit, toolPrefixPrev, true).reasonKey, 'cache_miss_unknown',
          'a tool past the breakpoint is outside the cached prefix');

        // The same shape, but the edit lands on the cached first tool.
        function firstToolEditTurn(opts) {
          const t1 = { name: 'Read', input_schema: { type: 'object', properties: { path: { type: opts.type } } },
            cache_control: { type: 'ephemeral' } };
          const t2 = { name: 'Write', input_schema: { type: 'object', properties: { text: { type: 'string' } } } };
          return turn({ ...opts, noBreakpoint: true, system: SYS, tools: [t1, t2] });
        }
        const cachedToolPrev = firstToolEditTurn({ id: 'r25', ts: '2026-08-14T10:00:00Z', type: 'string', usage: SEED });
        const cachedToolEdit = firstToolEditTurn({ id: 'r26', ts: '2026-08-14T10:00:30Z', type: 'number', usage: COLD });
        assert.equal(diagnose(cachedToolEdit, cachedToolPrev, true).reasonKey, 'cache_miss_tools',
          'an edit inside the cached tool prefix is the cause');

        // An array system prompt with the breakpoint on its first block: a change
        // to the second block was never cached.
        function twoBlockSysTurn(opts) {
          const e = { ...opts };
          delete e.tail;
          const t = turn({ ...e, noBreakpoint: true, system: SYS });
          t.request.body.system = [
            { type: 'text', text: SYS, cache_control: { type: 'ephemeral' } },
            { type: 'text', text: opts.tail },
          ];
          return t;
        }
        const sysTailPrev = twoBlockSysTurn({ id: 'r27', ts: '2026-08-14T10:00:00Z', tail: 'Today is Monday.', usage: SEED });
        const sysTailCur = twoBlockSysTurn({ id: 'r28', ts: '2026-08-14T10:00:30Z', tail: 'Today is Tuesday.', usage: COLD });
        assert.equal(diagnose(sysTailCur, sysTailPrev, true).reasonKey, 'cache_miss_unknown',
          'a system block past the breakpoint is outside the cached prefix');

        /* ── A breakpoint inside a message bounds that message too ── */
        function blockBpMsg(text, tail) {
          return { role: 'user', content: [
            { type: 'text', text, cache_control: { type: 'ephemeral' } },
            { type: 'text', text: tail },
          ] };
        }
        const blockPrev = turn({
          id: 'r29', ts: '2026-08-14T10:00:00Z',
          messages: [blockBpMsg('hello', 'appended context A')], usage: SEED,
        });
        const blockCur = turn({
          id: 'r30', ts: '2026-08-14T10:00:30Z',
          messages: [blockBpMsg('hello', 'appended context B')], usage: COLD,
        });
        assert.equal(diagnose(blockCur, blockPrev, true).reasonKey, 'cache_miss_unknown',
          'a content block after the breakpoint cannot invalidate it');
        const blockEdited = turn({
          id: 'r31', ts: '2026-08-14T10:00:30Z',
          messages: [blockBpMsg('a different opening', 'appended context A')], usage: COLD,
        });
        assert.equal(diagnose(blockEdited, blockPrev, true).reasonKey, 'cache_miss_unknown',
          'an unchanged earlier system checkpoint still should have produced reads');
        const blockOnlyPrev = turn({
          id: 'r29b', ts: '2026-08-14T10:00:00Z', noBreakpoint: true,
          messages: [blockBpMsg('hello', 'appended context A')], usage: SEED,
        });
        const blockOnlyEdited = turn({
          id: 'r31b', ts: '2026-08-14T10:00:30Z', noBreakpoint: true,
          messages: [blockBpMsg('a different opening', 'appended context A')], usage: COLD,
        });
        assert.equal(diagnose(blockOnlyEdited, blockOnlyPrev, true).reasonKey, 'cache_miss_history',
          'a change to the block carrying the breakpoint is the cause');

        /* ── Bedrock Converse marks breakpoints with a standalone cachePoint ── */
        function converseTurn(opts) {
          const t = turn({ ...opts, noBreakpoint: true, system: SYS });
          t.request.body.messages = [
            { role: 'user', content: [{ text: opts.text }] },
            { role: 'user', content: [{ cachePoint: { type: 'default' } }] },
          ];
          return t;
        }
        const conversePrev = converseTurn({ id: 'r32', ts: '2026-08-14T10:00:00Z', text: 'hello', usage: SEED });
        const converseCur = converseTurn({ id: 'r33', ts: '2026-08-14T10:00:30Z', text: 'different', usage: COLD });
        assert.equal(diagnose(converseCur, conversePrev, true).reasonKey, 'cache_miss_history',
          'a Bedrock cachePoint block marks the cached message extent');
        assert.equal(diagnose(conversePrev, null, false).reasonKey, 'cache_miss_initial',
          'a marker-only cachePoint user message is not a second opening turn');

        const firstBlockBody = {
          system: SYS,
          tools: TOOLS,
          messages: [{ role: 'user', content: [{ cachePoint: { type: 'default' } }, { text: 'hello' }] }],
        };
        const firstBlockScopes = context.cachedScopes(firstBlockBody, WARM);
        assert.equal(firstBlockScopes.msgs, 0,
          'a leading cachePoint covers no part of its own message');
        assert.equal(firstBlockScopes.tools, true,
          'but it still caches the preceding tool segment');
        assert.equal(firstBlockScopes.system, true,
          'and the preceding system segment');

        /* ── Converse tools live under toolConfig, with their own cachePoint ── */
        // The specs are nested where body.tools would never find them, and the
        // breakpoint is a standalone marker following the specs it caches.  Read
        // only from body.tools, a tool-scoped Converse cache has unknown extent,
        // so a schema edit inside it reads as unknown -- or a later history change
        // gets blamed for a miss the tool change caused.
        function converseToolTurn(opts) {
          const t = turn({ ...opts, noBreakpoint: true, system: SYS, tools: [] });
          t.request.body.toolConfig = {
            tools: [
              { toolSpec: { name: 'read_file', description: 'Read a file',
                inputSchema: { json: { type: 'object', properties: { path: { type: opts.type } } } } } },
              { cachePoint: { type: 'default' } },
            ],
          };
          t.request.body.messages = [{ role: 'user', content: [{ text: 'hello' }] }];
          return t;
        }
        const converseToolPrev = converseToolTurn({ id: 'r35', ts: '2026-08-14T10:00:00Z', type: 'string', usage: SEED });
        const converseToolEdit = converseToolTurn({ id: 'r36', ts: '2026-08-14T10:00:30Z', type: 'number', usage: COLD });
        assert.equal(diagnose(converseToolEdit, converseToolPrev, true).reasonKey, 'cache_miss_tools',
          'a Converse toolConfig cachePoint marks the cached tool prefix');

        // The marker caches the specs ahead of it and nothing after, so a spec
        // that follows it is outside the cached prefix.
        function converseTailToolTurn(opts) {
          const t = converseToolTurn({ ...opts, type: 'string' });
          t.request.body.toolConfig.tools.push({
            toolSpec: { name: 'write_file', description: 'Write a file',
              inputSchema: { json: { type: 'object', properties: { text: { type: opts.type } } } } },
          });
          return t;
        }
        const tailToolPrev = converseTailToolTurn({ id: 'r37', ts: '2026-08-14T10:00:00Z', type: 'string', usage: SEED });
        const tailToolEdit = converseTailToolTurn({ id: 'r38', ts: '2026-08-14T10:00:30Z', type: 'number', usage: COLD });
        assert.equal(diagnose(tailToolEdit, tailToolPrev, true).reasonKey, 'cache_miss_unknown',
          'a Converse tool spec past the cachePoint is outside the cached prefix');

        /* ── Relocating an intermediate cachePoint is not a content edit ── */
        /* Bedrock states a breakpoint as its own array element.  Stripping the
           marker key in place left an empty `{}` holding that slot, so moving an
           intermediate marker shifted the placeholder and compared unequal --
           reporting a *definite* tool edit against two identical spec lists.  The
           tail marker keeps the cached extent the same on both sides, so this is
           the shape where only the placeholder moved.  Where breakpoints sit is
           cachedScopes' concern; the content diff is only about content. */
        const cpBlock = () => ({ cachePoint: { type: 'default' } });
        const cpSpec = (name) => ({ toolSpec: { name, description: `${name} a file`,
          inputSchema: { json: { type: 'object', properties: { path: { type: 'string' } } } } } });
        function markerBody(tools) {
          return { toolConfig: { tools }, messages: [{ role: 'user', content: [{ text: 'hello' }] }] };
        }
        const markerPrevTools = [cpSpec('read_file'), cpBlock(), cpSpec('write_file'), cpSpec('list_dir'), cpBlock()];
        const markerCurTools = [cpSpec('read_file'), cpSpec('write_file'), cpBlock(), cpSpec('list_dir'), cpBlock()];
        const markerPrevBody = markerBody(markerPrevTools);
        const markerCurBody = markerBody(markerCurTools);
        const markerPrevScopes = context.cachedScopes(markerPrevBody, WARM);
        const markerCurScopes = context.cachedScopes(markerCurBody, WARM);
        assert.equal(markerPrevScopes.toolCount, markerCurScopes.toolCount,
          'the tail marker keeps the cached tool extent identical on both sides');
        assert.equal(
          context.diffCachedRegion(markerPrevBody, markerCurBody, markerPrevScopes, markerCurScopes).toolsChanged,
          false, 'moving an intermediate cachePoint is not a tool content edit');

        // The marker must not mask a real edit that moved along with it.
        const markerEditedTools = markerCurTools.map(b => JSON.parse(JSON.stringify(b)));
        markerEditedTools[0].toolSpec.description = 'Read a different file';
        const markerEditedBody = markerBody(markerEditedTools);
        assert.equal(
          context.diffCachedRegion(markerPrevBody, markerEditedBody, markerPrevScopes,
            context.cachedScopes(markerEditedBody, WARM)).toolsChanged,
          true, 'a real spec edit is still reported when a marker moved with it');

        // A block carrying a marker *and* content is content: it keeps its place.
        const markerWithText = context.normalizeCacheable([{ cachePoint: { type: 'default' }, text: 'A' }]);
        assert.equal(markerWithText.length, 1,
          'a block with a marker beside real content is not dropped');
        assert.equal(markerWithText[0].text, 'A',
          'the content beside a marker survives normalization');
        assert.equal(context.normalizeCacheable([cpBlock()]).length, 0,
          'a marker-only element is dropped rather than left as an empty object');

        /* ── A cached region of a different size names no cause ── */
        /* Naming a cause means comparing like with like.  When the two requests
           asked for different extents there is no such comparison to make: the
           shared portion compares equal while the part only one side cached is
           exactly what differs, and blaming the difference in extent would need
           to know which of several declared breakpoints the provider matched,
           which the trace does not record.  So the card says unknown either way
           -- shrunk or grown -- rather than guessing which reading applies. */
        function blockMsg(role, text) {
          return { role, content: [{ type: 'text', text }] };
        }

        // The predecessor cached [A, B]; this request moves its breakpoint back to A.
        const twoCached = turn({
          id: 'r39', ts: '2026-08-14T10:00:00Z',
          messages: [blockMsg('user', 'hello'), cachedMsg('assistant', 'hi')], usage: SEED,
        });
        const truncated = turn({
          id: 'r40', ts: '2026-08-14T10:00:30Z',
          messages: [cachedMsg('user', 'hello')], usage: COLD,
        });
        assert.equal(diagnose(truncated, twoCached, true).reasonKey, 'cache_miss_unknown',
          'a cached region the two requests sized differently supports no named cause');
        const truncatedOnlyPrev = turn({
          id: 'r39b', ts: '2026-08-14T10:00:00Z', noBreakpoint: true,
          messages: [blockMsg('user', 'hello'), cachedMsg('assistant', 'hi')], usage: SEED,
        });
        const truncatedOnly = turn({
          id: 'r40b', ts: '2026-08-14T10:00:30Z', noBreakpoint: true,
          messages: [cachedMsg('user', 'hello')], usage: COLD,
        });
        assert.equal(diagnose(truncatedOnly, truncatedOnlyPrev, true).reasonKey, 'cache_miss_unknown',
          'a shorter prefix is unknown even with no competing checkpoint ahead of it');

        // Growing the prefix is the normal incremental path, and equally unnamed.
        const grown = turn({
          id: 'r42', ts: '2026-08-14T10:00:30Z',
          messages: [cachedMsg('user', 'hello'), cachedMsg('assistant', 'hi')], usage: COLD,
        });
        const oneCached = turn({
          id: 'r43', ts: '2026-08-14T10:00:00Z',
          messages: [cachedMsg('user', 'hello')], usage: SEED,
        });
        assert.equal(diagnose(grown, oneCached, true).reasonKey, 'cache_miss_unknown',
          'extending the breakpoint forward does not invalidate the prefix it builds on');

        /* An edit *inside* a region both sides sized the same is still named: the
           narrowing must not have turned the feature off. */
        const sameExtentPrev = turn({
          id: 'r43b', ts: '2026-08-14T10:00:00Z', noBreakpoint: true,
          messages: [blockMsg('user', 'hello'), cachedMsg('assistant', 'hi')], usage: SEED,
        });
        const sameExtentEdited = turn({
          id: 'r43c', ts: '2026-08-14T10:00:30Z', noBreakpoint: true,
          messages: [blockMsg('user', 'a different opening'), cachedMsg('assistant', 'hi')], usage: COLD,
        });
        assert.equal(diagnose(sameExtentEdited, sameExtentPrev, true).reasonKey, 'cache_miss_history',
          'an edit inside a region both sides cached to the same depth is still named');

        /* ── Mixed tiers: creation-only metadata must not shorten the chain ── */
        // A turn reusing a 1-hour prefix while appending a new 5-minute tail bills
        // only the 5-minute tokens it wrote.  Reading that as the whole chain's
        // lifetime makes the 1-hour prefix look expired after six minutes, so an
        // edit inside it is suppressed and expiry is reported instead.
        const mixedTier = turn({
          id: 'r44', ts: '2026-08-14T10:00:00Z', ttl: '1h',
          usage: { ...SEED, cache_creation: { ephemeral_5m_input_tokens: 718, ephemeral_1h_input_tokens: 0 } },
        });
        const sixMinLater = turn({
          id: 'r45', ts: '2026-08-14T10:06:00Z', ttl: '1h',
          tools: [{ name: 'Read', input_schema: { type: 'object', properties: { path: { type: 'number' } } } }],
          usage: COLD,
        });
        assert.equal(diagnose(sixMinLater, mixedTier, true).reasonKey, 'cache_miss_tools',
          'a 1h prefix is still live at six minutes, so the tool edit is the cause');

        // The 1-hour tier still governs when nothing changed: no expiry claim yet.
        const idleSixMin = turn({ id: 'r46', ts: '2026-08-14T10:06:00Z', ttl: '1h', usage: COLD });
        assert.equal(diagnose(idleSixMin, mixedTier, true).reasonKey, 'cache_miss_unknown',
          'the longest declared tier decides, not the bucket that happened to be written');

        // And past the hour it does expire, reported as the 1-hour tier.
        const idlePastHour = turn({ id: 'r47', ts: '2026-08-14T11:30:00Z', ttl: '1h', usage: COLD });
        const pastHourDiag = diagnose(idlePastHour, mixedTier, true);
        assert.equal(pastHourDiag.reasonKey, 'cache_miss_ttl');
        assert.ok(pastHourDiag.reasonText.includes('60'), 'the 1h tier reports 60 minutes');

        // A response-declared 1h tier still outranks a 5-minute request default,
        // which is the case the earlier response-wins assertion covers; the
        // combination only ever takes the longer of the two.
        const respLongReqShort = turn({
          id: 'r48', ts: '2026-08-14T10:00:00Z',
          usage: { ...SEED, cache_creation: { ephemeral_1h_input_tokens: 35115 } },
        });
        assert.equal(diagnose(idleSixMin, respLongReqShort, true).reasonKey, 'cache_miss_unknown',
          'a response-side 1h tier is still live at six minutes');

        /* ── A capture that begins mid-session has an earlier cache it cannot see ── */
        const resumed = turn({
          id: 'r34', ts: '2026-08-14T10:00:00Z',
          messages: [
            { role: 'user', content: 'hello' },
            { role: 'assistant', content: 'hi' },
            { role: 'user', content: 'carry on' },
          ],
          usage: COLD,
        });
        assert.equal(diagnose(resumed, null, false).reasonKey, 'cache_miss_unknown',
          'a replayed history means the conversation started before the capture');
        assert.equal(diagnose(resumed, null, false).lowConfidence, true);

        /* ── Engines that embed cache reads in input_tokens report no write counter ── */
        const embedded = turn({
          id: 'r14', ts: '2026-08-14T10:01:00Z',
          usage: { input_tokens: 900, output_tokens: 30, cache_creation_input_tokens: 120, _cache_read_in_input: true },
        });
        assert.equal(diagnose(embedded, seed, true), null,
          'a write counter cannot be interpreted when reads are embedded in input_tokens');

        /* ── Predecessor selection ── */
        // Prompt caches are per-model, so a switch starts cold however similar
        // the prompts look; the earlier model's turn is not a candidate.
        const switched = turn({ id: 'r15', ts: '2026-08-14T10:00:30Z', model: 'claude-sonnet-5', usage: COLD });
        loadEntries([seed, switched]);
        assert.equal(findPredecessor(switched).entry, null, 'a different model never shares a cache');
        assert.equal(diagnose(switched, findPredecessor(switched).entry, false).reasonKey, 'cache_miss_initial');

        // A turn that neither read nor wrote the cache left nothing to reuse.
        const noCache = turn({ id: 'r16', ts: '2026-08-14T10:00:00Z', usage: { input_tokens: 1200, output_tokens: 40 } });
        const afterNoCache = turn({ id: 'r17', ts: '2026-08-14T10:00:30Z', usage: COLD });
        loadEntries([noCache, afterNoCache]);
        assert.equal(findPredecessor(afterNoCache).entry, null, 'a turn without cache activity is not a predecessor');
        assert.equal(diagnose(afterNoCache, findPredecessor(afterNoCache).entry, false).reasonKey, 'cache_miss_initial');

        // Concurrent conversations can share a system prompt, so the nearest
        // earlier turn from another session must not be treated as the owner.
        const otherSession = turn({ id: 'r18', ts: '2026-08-14T10:00:10Z', session: 'sess-other', usage: SEED });
        const mine = turn({ id: 'r19', ts: '2026-08-14T10:00:20Z', usage: COLD });
        loadEntries([seed, otherSession, mine]);
        assert.equal(findPredecessor(mine).entry.request_id, 'r1', 'the predecessor must come from the same session');
        assert.equal(findPredecessor(mine).exact, true, 'a shared session identifier confirms the predecessor');

        // Claude Code rewrites earlier messages as a session grows — reminders
        // move, tool results get compacted — so a genuine predecessor often
        // shares no message prefix at all.  Measured on the 138-turn reference
        // trace, the two expiry turns first differ from their predecessor at
        // message 8 and 37.  The session identifier is what confirms them.
        const rewrittenHead = turn({
          id: 'r20', ts: '2026-08-14T10:07:30Z',
          messages: [{ role: 'user', content: 'hello <system-reminder>moved</system-reminder>' }],
          usage: COLD,
        });
        loadEntries([seed, rewrittenHead]);
        const headPred = findPredecessor(rewrittenHead);
        assert.equal(headPred.entry.request_id, 'r1');
        assert.equal(headPred.exact, true,
          'a shared session confirms a predecessor whose earlier messages were rewritten');

        // Sidebar filters are a viewing choice: hiding a turn must not change
        // the diagnosis of one that is still visible.
        loadEntries([seed, mine]);
        const unfilteredDiag = diagnose(mine, findPredecessor(mine).entry, findPredecessor(mine).exact);
        setFiltered([mine]);
        const filteredPred = findPredecessor(mine);
        assert.equal(filteredPred.entry.request_id, 'r1', 'the predecessor search must walk the unfiltered history');
        assert.deepEqual(diagnose(mine, filteredPred.entry, filteredPred.exact), unfilteredDiag,
          'filtering the sidebar must not change a cache verdict');

        /* ── A first cache write need not be the session's first turn ── */
        /* A short prompt stays under the provider's minimum cacheable size, so the
           opening turns write nothing and are correctly skipped as predecessors.
           The turn that finally crosses the threshold carries the whole replayed
           exchange, so judging by its own message count alone called this a
           mid-session capture and the card fell back to unknown -- even though the
           capture contains the session's start and proves nothing cached earlier. */
        const shortOpen = turn({
          id: 'r61', ts: '2026-08-14T11:00:00Z',
          messages: [{ role: 'user', content: 'hi' }],
          usage: { input_tokens: 300, output_tokens: 12 },
        });
        const shortSecond = turn({
          id: 'r62', ts: '2026-08-14T11:00:20Z',
          messages: [{ role: 'user', content: 'hi' }, { role: 'assistant', content: 'hello' },
            { role: 'user', content: 'and now?' }],
          usage: { input_tokens: 700, output_tokens: 20 },
        });
        const firstWrite = turn({
          id: 'r63', ts: '2026-08-14T11:00:40Z',
          messages: [{ role: 'user', content: 'hi' }, { role: 'assistant', content: 'hello' },
            { role: 'user', content: 'and now?' }, { role: 'assistant', content: 'a long answer' },
            { role: 'user', content: 'go on' }],
          usage: COLD,
        });
        loadEntries([shortOpen, shortSecond, firstWrite]);
        const firstWritePred = findPredecessor(firstWrite);
        assert.equal(firstWritePred.entry, null,
          'turns that never cached anything are not predecessors');
        assert.equal(diagnose(firstWrite, firstWritePred.entry, firstWritePred.exact).reasonKey,
          'cache_miss_initial',
          'a capture holding the session start proves the first write, whatever the message count');

        /* A mid-session capture still cannot claim it: no earlier entry of this
           session shows the conversation opening, so an unseen cache may exist. */
        loadEntries([shortSecond, firstWrite]);
        assert.equal(diagnose(firstWrite, findPredecessor(firstWrite).entry, false).reasonKey,
          'cache_miss_unknown',
          'without the session start an earlier cache cannot be ruled out');

        /* Nor can it be claimed across sessions: another conversation's opening
           turn says nothing about this one's cache. */
        const otherOpen = turn({
          id: 'r64', ts: '2026-08-14T11:00:00Z', session: 'sess-elsewhere',
          messages: [{ role: 'user', content: 'hi' }],
          usage: { input_tokens: 300, output_tokens: 12 },
        });
        loadEntries([otherOpen, firstWrite]);
        assert.equal(diagnose(firstWrite, findPredecessor(firstWrite).entry, false).reasonKey,
          'cache_miss_unknown',
          'another session opening is not this session opening');

        /* ── A model named only in a nested Vertex path ── */
        /* Vertex puts the model under the publisher rather than at `/v1/models/`,
           so requiring the shallow form reported no model for every rawPredict
           turn. Two different models then looked like one cache chain and the
           later one collected a structural or TTL verdict it had not earned. */
        const vertexTurn = (id, ts, model, usage) => ({
          request_id: id, timestamp: ts,
          request: {
            method: 'POST',
            path: `/v1/projects/p/locations/us-east5/publishers/anthropic/models/${model}:rawPredict`,
            headers: { 'X-Claude-Code-Session-Id': SESSION },
            body: {
              system: [{ type: 'text', text: SYS, cache_control: { type: 'ephemeral' } }],
              tools: TOOLS,
              messages: [{ role: 'user', content: 'hello' }],
            },
          },
          response: { body: { usage } },
        });
        const vertexSeed = vertexTurn('r70', '2026-08-14T12:00:00Z', 'claude-opus-4-7', SEED);
        const vertexSwitched = vertexTurn('r71', '2026-08-14T12:00:30Z', 'claude-sonnet-4-5', COLD);
        loadEntries([vertexSeed, vertexSwitched]);
        assert.equal(findPredecessor(vertexSwitched).entry, null,
          'a Vertex model switch must not be read as one cache chain');
        const vertexSame = vertexTurn('r72', '2026-08-14T12:00:30Z', 'claude-opus-4-7', COLD);
        loadEntries([vertexSeed, vertexSame]);
        assert.equal(findPredecessor(vertexSame).entry?.request_id, 'r70',
          'the same Vertex model still shares a cache chain');

        /* ── Duplicate display names in the tool list ── */
        /* Deduplicating by display name drops every definition after the first, so
           editing a later duplicate inside the cached prefix left the comparison
           equal and the cold write was reported as unknown. The cache is keyed on
           the bytes the request sent, duplicates included. */
        const dupTools = [
          { name: 'mcp__a__run', input_schema: { type: 'object', properties: { x: { type: 'string' } } } },
          { name: 'mcp__a__run', input_schema: { type: 'object', properties: { y: { type: 'string' } } } },
        ];
        const dupEdited = [
          dupTools[0],
          { name: 'mcp__a__run', input_schema: { type: 'object', properties: { y: { type: 'number' } } } },
        ];
        const dupSeed = turn({ id: 'r73', ts: '2026-08-14T12:10:00Z', tools: dupTools, usage: SEED });
        const dupChanged = turn({ id: 'r74', ts: '2026-08-14T12:10:30Z', tools: dupEdited, usage: COLD });
        assert.equal(diagnose(dupChanged, dupSeed, true).reasonKey, 'cache_miss_tools',
          'editing a duplicate-named tool inside the cached prefix is a tool change');

        /* ── A cached system prefix of a different size ── */
        /* The predecessor cached [A, B] with its only breakpoint on B; this request
           sends [A].  Comparing the surviving block finds A equal, so the content
           says nothing, and the change in extent is not a cause the trace can
           attribute -- so the system segment obeys the same rule as the messages:
           different extents, no named cause. */
        const twoBlockSystem = (bpOnFirst) => [
          { type: 'text', text: SYS, ...(bpOnFirst ? { cache_control: { type: 'ephemeral' } } : {}) },
          { type: 'text', text: 'Project rules: be terse.', cache_control: { type: 'ephemeral' } },
        ];
        const sysBody = (system, usage, id, ts) => ({
          request_id: id, timestamp: ts,
          request: {
            method: 'POST', path: '/v1/messages',
            headers: { 'X-Claude-Code-Session-Id': SESSION },
            body: { model: 'claude-opus-5', system, tools: TOOLS, messages: [{ role: 'user', content: 'hello' }] },
          },
          response: { body: { usage } },
        });
        const bothBlocks = sysBody(twoBlockSystem(false), SEED, 'r75', '2026-08-14T12:20:00Z');
        const sysTruncated = sysBody(
          [{ type: 'text', text: SYS, cache_control: { type: 'ephemeral' } }],
          COLD, 'r76', '2026-08-14T12:20:30Z',
        );
        assert.equal(diagnose(sysTruncated, bothBlocks, true).reasonKey, 'cache_miss_unknown',
          'a system prefix the two requests sized differently supports no named cause');
        assert.notEqual(diagnose(sysTruncated, bothBlocks, true).reasonKey, 'cache_miss_system',
          'the surviving blocks comparing equal must not read as no system change');

        /* An edit inside a system prefix both sides sized the same is still named. */
        const sysEditedPrev = sysBody(
          [{ type: 'text', text: SYS, cache_control: { type: 'ephemeral' } }],
          SEED, 'r77', '2026-08-14T12:21:00Z',
        );
        const sysEdited = sysBody(
          [{ type: 'text', text: SYS + ' Always answer in French.', cache_control: { type: 'ephemeral' } }],
          COLD, 'r78', '2026-08-14T12:21:30Z',
        );
        assert.equal(diagnose(sysEdited, sysEditedPrev, true).reasonKey, 'cache_miss_system',
          'an edit inside a system prefix both sides cached to the same depth is named');

        /* ── The exact match is worth re-asking once a payload has arrived ── */
        /* In remote dashboard mode a candidate is a metadata stub: its headers
           and messages are synthesized, so the session identifier and the message
           hashes that confirm a predecessor are both absent and the search can only
           record `exact: false`.  Every structural and TTL verdict is gated on that
           flag, so forwarding it after the fetch would leave a multi-message Claude
           turn permanently `unknown` however complete the payload is.  Asking the
           same question against the real bodies is what makes the fetch worth
           doing, which is why the check is a function of its own. */
        const stubPrev = { _isStub: true, _rawIdx: 901, request: { body: {} }, response: { body: {} } };
        assert.equal(isExactPredecessor(mine, stubPrev), false,
          'a stub carries no session header and no messages, so nothing is confirmable');
        assert.equal(isExactPredecessor(mine, seed), true,
          'the same question against the fetched payload confirms the shared session');

        // A hashed message prefix confirms it too, for providers with no session
        // header at all.
        const anonPrev = turn({
          id: 'r49', ts: '2026-08-14T10:00:00Z', session: '',
          messages: [{ role: 'user', content: 'hello' }], usage: SEED,
        });
        const anonCur = turn({
          id: 'r50', ts: '2026-08-14T10:00:30Z', session: '',
          messages: [{ role: 'user', content: 'hello' }, { role: 'assistant', content: 'hi' }],
          usage: COLD,
        });
        assert.equal(isExactPredecessor(anonCur, anonPrev), true,
          'a hashed message prefix confirms a predecessor with no session header');

        // Adjacency alone is not evidence: a turn from another conversation that
        // shares no link, session, or prefix must not be confirmed.
        const unrelated = turn({
          id: 'r51', ts: '2026-08-14T10:00:10Z', session: 'sess-other',
          messages: [{ role: 'user', content: 'an entirely unrelated opening' }], usage: SEED,
        });
        assert.equal(isExactPredecessor(mine, unrelated), false,
          'a neighbouring turn from another session is not confirmed by adjacency');

        /* ── A 500-char hash collision is not an exact predecessor ── */
        const sharedPrefix = 'system-reminder '.repeat(40);
        const hashA = { role: 'user', content: sharedPrefix + 'CONVERSATION_A_UNIQUE_TAIL' };
        const hashB = { role: 'user', content: sharedPrefix + 'CONVERSATION_B_UNIQUE_TAIL' };
        assert.equal(context._msgHash(hashA), context._msgHash(hashB),
          'the truncated hash still collides; exactness must not use it');
        const longAnonPrev = turn({
          id: 'r52', ts: '2026-08-14T10:00:00Z', session: '',
          messages: [hashA], usage: SEED,
        });
        const longAnonCur = turn({
          id: 'r53', ts: '2026-08-14T10:00:30Z', session: '',
          messages: [hashB, { role: 'assistant', content: 'hi' }], usage: COLD,
        });
        loadEntries([longAnonPrev, longAnonCur]);
        const skippedInexact = findPredecessor(longAnonCur);
        assert.equal(skippedInexact.entry.request_id, 'r52',
          'an inexact headerless neighbor is kept as fallback when nothing exact remains');
        assert.equal(skippedInexact.exact, false);

        const exactOlder = turn({
          id: 'r53b', ts: '2026-08-14T09:59:00Z', session: '',
          messages: [{ role: 'user', content: 'headerless exact opening' }], usage: SEED,
        });
        const inexactNear = turn({
          id: 'r53c', ts: '2026-08-14T10:00:00Z', session: '',
          messages: [hashA], usage: SEED,
        });
        const headerlessCur = turn({
          id: 'r53d', ts: '2026-08-14T10:00:30Z', session: '',
          messages: [{ role: 'user', content: 'headerless exact opening' }, { role: 'assistant', content: 'hi' }],
          usage: COLD,
        });
        loadEntries([exactOlder, inexactNear, headerlessCur]);
        const preferred = findPredecessor(headerlessCur);
        assert.equal(preferred.entry.request_id, 'r53b',
          'an older exact match outranks a nearer inexact headerless neighbor');
        assert.equal(preferred.exact, true);

        assert.equal(isExactPredecessor(longAnonCur, longAnonPrev), false,
          'a truncated-hash match must stay inexact when the full messages differ');
        assert.equal(diagnose(longAnonCur, longAnonPrev, isExactPredecessor(longAnonCur, longAnonPrev)).reasonKey,
          'cache_miss_unknown',
          'an inexact long-prefix neighbor cannot support a structural diagnosis');

        /* ── Empty tool lists are still compared when a later breakpoint covers them ── */
        function msgCachedTurn(opts) {
          return turn({
            ...opts,
            noBreakpoint: true,
            system: SYS,
            messages: [cachedMsg('user', opts.text || 'hello')],
          });
        }
        const emptyToolsPrev = msgCachedTurn({
          id: 'r54', ts: '2026-08-14T10:00:00Z', tools: [], usage: SEED,
        });
        const firstToolCur = msgCachedTurn({
          id: 'r55', ts: '2026-08-14T10:00:30Z',
          tools: [{ name: 'Read', input_schema: { type: 'object', properties: { path: { type: 'string' } } } }],
          usage: COLD,
        });
        assert.equal(diagnose(firstToolCur, emptyToolsPrev, true).reasonKey, 'cache_miss_tools',
          'adding the first tool under a message-level breakpoint is a tool change');
        const lastToolRemoved = msgCachedTurn({
          id: 'r56', ts: '2026-08-14T10:00:40Z', tools: [], usage: COLD,
        });
        assert.equal(diagnose(lastToolRemoved, firstToolCur, true).reasonKey, 'cache_miss_tools',
          'removing the last tool under a message-level breakpoint is a tool change');

        /* ── A cached tool prefix of a different size ── */
        /* The tool segment follows the same rule as the system and message ones:
           the predecessor cached [A, B] with its only breakpoint on B, this
           request declares its own on A.  The extents differ, so no cause is
           named -- the surviving spec compares equal and the dropped one is
           outside what this request asked to cache. */
        const twoTools = [
          { name: 'Read', input_schema: { type: 'object', properties: { path: { type: 'string' } } } },
          { name: 'Write', input_schema: { type: 'object', properties: { text: { type: 'string' } } } },
        ];
        const toolBpTurn = (id, ts, tools, bpAt, usage) => ({
          request_id: id, timestamp: ts,
          request: {
            method: 'POST', path: '/v1/messages',
            headers: { 'X-Claude-Code-Session-Id': SESSION },
            body: {
              model: 'claude-opus-5',
              system: SYS,
              tools: tools.map((spec, i) => (i === bpAt ? { ...spec, cache_control: { type: 'ephemeral' } } : spec)),
              messages: [{ role: 'user', content: 'hello' }],
            },
          },
          response: { body: { usage } },
        });
        const bothTools = toolBpTurn('r80', '2026-08-14T13:00:00Z', twoTools, 1, SEED);
        const toolTruncated = toolBpTurn('r81', '2026-08-14T13:00:30Z', twoTools.slice(0, 1), 0, COLD);
        assert.equal(diagnose(toolTruncated, bothTools, true).reasonKey, 'cache_miss_unknown',
          'a tool prefix the two requests sized differently supports no named cause');

        // Growing the tool prefix is the normal incremental path.
        const toolGrown = toolBpTurn('r83', '2026-08-14T13:01:00Z', twoTools, 1, COLD);
        const oneToolCached = toolBpTurn('r84', '2026-08-14T13:00:00Z', twoTools.slice(0, 1), 0, SEED);
        assert.notEqual(diagnose(toolGrown, oneToolCached, true).reasonKey, 'cache_miss_tools',
          'extending the tool breakpoint forward does not invalidate the prefix it builds on');

        /* A spec edit inside a tool prefix both sides bounded identically is still
           named: only the extent comparison was narrowed, not the content one. */
        const toolEditedPrev = toolBpTurn('r85', '2026-08-14T13:02:00Z', twoTools, 1, SEED);
        const toolEdited = toolBpTurn('r86', '2026-08-14T13:02:30Z', [
          twoTools[0],
          { name: 'Write', input_schema: { type: 'object', properties: { text: { type: 'number' } } } },
        ], 1, COLD);
        assert.equal(diagnose(toolEdited, toolEditedPrev, true).reasonKey, 'cache_miss_tools',
          'a schema edit inside an identically bounded tool prefix is still named');

        /* ── A breakpoint moved back inside one message ── */
        /* Both requests send the same message, but the predecessor's breakpoint sat
           on its second block and this one's sits on the first.  The cached region
           therefore ends at a different place inside the message, which is the
           same mismatch of extent one level down: no cause is named. */
        const twoBlockMsg = (bpIdx) => ({
          role: 'user',
          content: [
            { type: 'text', text: 'first block', ...(bpIdx === 0 ? { cache_control: { type: 'ephemeral' } } : {}) },
            { type: 'text', text: 'second block', ...(bpIdx === 1 ? { cache_control: { type: 'ephemeral' } } : {}) },
          ],
        });
        const intraBlockPrev = turn({
          id: 'r87', ts: '2026-08-14T13:10:00Z', noBreakpoint: true,
          messages: [twoBlockMsg(1)], usage: SEED,
        });
        const blockTruncated = turn({
          id: 'r88', ts: '2026-08-14T13:10:30Z', noBreakpoint: true,
          messages: [twoBlockMsg(0)], usage: COLD,
        });
        assert.equal(diagnose(blockTruncated, intraBlockPrev, true).reasonKey, 'cache_miss_unknown',
          'a breakpoint on a different block ends the cached region elsewhere');

        // Extending the breakpoint to a later block of the same message is growth.
        assert.notEqual(diagnose(
          turn({ id: 'r89', ts: '2026-08-14T13:12:00Z', noBreakpoint: true, messages: [twoBlockMsg(1)], usage: COLD }),
          turn({ id: 'r8a', ts: '2026-08-14T13:11:30Z', noBreakpoint: true, messages: [twoBlockMsg(0)], usage: SEED }),
          true,
        ).reasonKey, 'cache_miss_history',
          'moving the breakpoint forward inside a message extends the prefix');

        /* An edit to a block both sides cached is still named: the breakpoint sits on
           block 1 in both requests, so the whole message is inside the compared
           region and the changed first block is a real content difference. */
        const editedBlockMsg = (firstText) => ({
          role: 'user',
          content: [
            { type: 'text', text: firstText },
            { type: 'text', text: 'second block', cache_control: { type: 'ephemeral' } },
          ],
        });
        assert.equal(diagnose(
          turn({ id: 'r8b', ts: '2026-08-14T13:13:30Z', noBreakpoint: true, messages: [editedBlockMsg('rewritten block')], usage: COLD }),
          turn({ id: 'r8c', ts: '2026-08-14T13:13:00Z', noBreakpoint: true, messages: [editedBlockMsg('first block')], usage: SEED }),
          true,
        ).reasonKey, 'cache_miss_history',
          'an edit inside an identically bounded message is still named');

        /* ── An unchanged earlier message checkpoint outranks a later edit ── */
        /* Both sides cache message 0 and message 1.  The edit lands in message 1,
           so the entry at message 0 should still have matched and produced reads.
           Zero reads contradict that, so naming the message edit would give a
           definite cause the trace refutes. */
        const twoMsgCheckpoints = (tailText, id, ts, usage) => turn({
          id, ts, noBreakpoint: true,
          messages: [cachedMsg('user', 'shared opening'), cachedMsg('assistant', tailText)],
          usage,
        });
        const msgCkptPrev = twoMsgCheckpoints('first tail', 'r90', '2026-08-14T13:20:00Z', SEED);
        const msgCkptCur = twoMsgCheckpoints('edited tail', 'r91', '2026-08-14T13:20:30Z', COLD);
        assert.equal(diagnose(msgCkptCur, msgCkptPrev, true).reasonKey, 'cache_miss_unknown',
          'an unchanged earlier message checkpoint should have produced cache reads');

        /* With the only breakpoint on the edited message there is no earlier entry
           to contradict it, so the edit is the cause. */
        const soleCkptPrev = turn({
          id: 'r92', ts: '2026-08-14T13:21:00Z', noBreakpoint: true,
          messages: [blockMsg('user', 'shared opening'), cachedMsg('assistant', 'first tail')], usage: SEED,
        });
        const soleCkptCur = turn({
          id: 'r93', ts: '2026-08-14T13:21:30Z', noBreakpoint: true,
          messages: [blockMsg('user', 'shared opening'), cachedMsg('assistant', 'edited tail')], usage: COLD,
        });
        assert.equal(diagnose(soleCkptCur, soleCkptPrev, true).reasonKey, 'cache_miss_history',
          'with no earlier checkpoint the message edit is the cause');

        /* An edit in the first checkpointed message has nothing ahead of it. */
        const firstMsgEdited = turn({
          id: 'r94', ts: '2026-08-14T13:22:00Z', noBreakpoint: true,
          messages: [cachedMsg('user', 'a different opening'), cachedMsg('assistant', 'first tail')], usage: COLD,
        });
        assert.equal(diagnose(firstMsgEdited, msgCkptPrev, true).reasonKey, 'cache_miss_history',
          'an edit in the first checkpointed message has no earlier entry to contradict it');

        /* ── Cache markers are protocol metadata, not names to erase ── */
        /* `cache_control` is a marker only where the protocol puts it: on a tool
           spec, a system block, or a message content block.  The same key nested
           inside a tool schema or a cached tool_use input is ordinary payload, so
           erasing it recursively made an edited payload compare equal to its
           predecessor and the invalidation it caused fell through to unknown. */
        const schemaTools = (propType) => [{
          name: 'configure',
          input_schema: {
            type: 'object',
            properties: { cache_control: { type: propType }, cachePoint: { type: 'string' } },
          },
        }];
        const schemaSeed = turn({ id: 'r95', ts: '2026-08-14T13:30:00Z', tools: schemaTools('string'), usage: SEED });
        const schemaPropEdited = turn({ id: 'r96', ts: '2026-08-14T13:30:30Z', tools: schemaTools('number'), usage: COLD });
        assert.equal(diagnose(schemaPropEdited, schemaSeed, true).reasonKey, 'cache_miss_tools',
          'a tool schema property named cache_control is payload, not a marker');

        // Same for a message payload that happens to carry the key.
        const payloadMsg = (value) => ({
          role: 'assistant',
          content: [{
            type: 'tool_use', id: 'tu_1', name: 'configure',
            input: { cache_control: value, cachePoint: value },
            cache_control: { type: 'ephemeral' },
          }],
        });
        const payloadSeed = turn({
          id: 'r97', ts: '2026-08-14T13:31:00Z', noBreakpoint: true,
          messages: [blockMsg('user', 'run it'), payloadMsg('on')], usage: SEED,
        });
        const payloadEdited = turn({
          id: 'r98', ts: '2026-08-14T13:31:30Z', noBreakpoint: true,
          messages: [blockMsg('user', 'run it'), payloadMsg('off')], usage: COLD,
        });
        assert.equal(diagnose(payloadEdited, payloadSeed, true).reasonKey, 'cache_miss_history',
          'a tool_use input field named cache_control is payload, not a marker');

        // The markers themselves still have to be stripped, or moving one would
        // read as a content edit rather than as the truncation it is.
        const markerOnlyPrev = turn({
          id: 'r99', ts: '2026-08-14T13:32:00Z', noBreakpoint: true,
          messages: [cachedMsg('user', 'hello')], usage: SEED,
        });
        const markerOnlyCur = turn({
          id: 'r100', ts: '2026-08-14T13:32:30Z', noBreakpoint: true,
          messages: [cachedMsg('user', 'hello'), blockMsg('assistant', 'hi')], usage: COLD,
        });
        assert.notEqual(diagnose(markerOnlyCur, markerOnlyPrev, true).reasonKey, 'cache_miss_history',
          'appending an uncached message leaves the cached prefix identical');

        /* ── An interleaved conversation must not hide a provable first write ── */
        /* `findCachePredecessor` keeps a headerless cache-bearing neighbor as a
           fallback because it cannot rule it out.  The trace can still prove this
           write is the session's first: the capture holds the session opening and
           no same-session turn cached anything ahead of it.  Merely having an
           unrelated conversation interleaved used to turn that into unknown. */
        const headerlessOther = turn({
          id: 'r101', ts: '2026-08-14T14:00:00Z', session: '',
          messages: [{ role: 'user', content: 'an unrelated conversation entirely' }], usage: SEED,
        });
        const ownOpening = turn({
          id: 'r102', ts: '2026-08-14T14:00:10Z',
          messages: [{ role: 'user', content: 'hi' }],
          usage: { input_tokens: 300, output_tokens: 12 },
        });
        const ownFirstWrite = turn({
          id: 'r103', ts: '2026-08-14T14:00:20Z',
          messages: [{ role: 'user', content: 'hi' }, { role: 'assistant', content: 'hello' },
            { role: 'user', content: 'go on' }],
          usage: COLD,
        });
        loadEntries([headerlessOther, ownOpening, ownFirstWrite]);
        const fallbackPred = findPredecessor(ownFirstWrite);
        assert.equal(fallbackPred.entry.request_id, 'r101',
          'the headerless neighbor is still kept as a fallback');
        assert.equal(fallbackPred.exact, false);
        assert.equal(diagnose(ownFirstWrite, fallbackPred.entry, fallbackPred.exact).reasonKey,
          'cache_miss_initial',
          'an unrelated interleaved turn cannot hide a first write the trace proves');

        /* But a same-session turn that did cache leaves a real earlier entry, so
           the miss is not an initial write however inexact the caller's flag. */
        const sameSessionCached = turn({
          id: 'r104', ts: '2026-08-14T14:00:15Z',
          messages: [{ role: 'user', content: 'hi' }, { role: 'assistant', content: 'hello' }], usage: SEED,
        });
        loadEntries([ownOpening, sameSessionCached, ownFirstWrite]);
        assert.equal(diagnose(ownFirstWrite, headerlessOther, false).reasonKey, 'cache_miss_unknown',
          'an earlier same-session turn that cached rules out a first write');

        // And without the session opening in view an earlier cache cannot be ruled
        // out, whatever else is interleaved.
        loadEntries([headerlessOther, ownFirstWrite]);
        assert.equal(diagnose(ownFirstWrite, headerlessOther, false).reasonKey, 'cache_miss_unknown',
          'without the session opening an unseen earlier cache is still possible');

        /* ── Every locale defines the diagnostic strings ── */
        const required = ['cache_diag_title', 'cache_miss_system', 'cache_miss_tools',
          'cache_miss_history', 'cache_miss_ttl', 'cache_miss_initial', 'cache_miss_unknown',
          'cache_miss_pending'];
        for (const [loc, table] of Object.entries(i18n)) {
          for (const key of required) {
            assert.ok(table[key], `locale ${loc} is missing ${key}`);
          }
          assert.ok(table['cache_miss_ttl'].includes('{minutes}'),
            `locale ${loc} cache_miss_ttl must carry the {minutes} placeholder`);
        }

        /* ── Dashboard predecessor fetch: continue after an inexact fallback, and
              replace the pending card when the records API fails. ── */
        const upgrade = context.upgradeCacheDiagnostic;
        function pendingHost(idx, exact) {
          return {
            dataset: { pendingIdx: String(idx), pendingExact: exact ? '1' : '0' },
            isConnected: true,
            outerHTML: 'PENDING',
            remove() { this.removed = true; },
          };
        }
        function pendingRoot(host) {
          return { querySelector() { return host; } };
        }

        const unlabeledSeed = turn({
          id: 'r57', ts: '2026-08-14T10:00:00Z', session: '',
          usage: SEED,
        });
        const unlabeledOther = turn({
          id: 'r58', ts: '2026-08-14T10:00:10Z', session: '',
          messages: [{ role: 'user', content: 'an entirely unrelated opening' }],
          usage: SEED,
        });
        const dashboardCur = turn({
          id: 'r59', ts: '2026-08-14T10:00:40Z',
          tools: [{ name: 'Read', input_schema: { type: 'object', properties: { path: { type: 'number' } } } }],
          usage: COLD,
        });
        loadEntries([unlabeledSeed, unlabeledOther, dashboardCur]);
        const fallback = findPredecessor(dashboardCur);
        assert.equal(fallback.entry.request_id, 'r58',
          'an unlabeled neighbor is the fallback while session headers are missing');
        assert.equal(fallback.exact, false);
        const skipped = findPredecessor(dashboardCur, new Set([fallback.idx]));
        assert.equal(skipped.entry.request_id, 'r57',
          'skipping the rejected fallback continues the same predecessor walk');
        assert.equal(skipped.exact, true,
          'the older unlabeled turn is confirmed by a full message prefix, not a 500-char hash');

        (async () => {
          const host = pendingHost(fallback.idx, false);
          await upgrade(dashboardCur, pendingRoot(host));
          assert.ok(host.outerHTML.includes('data-reason="cache_miss_tools"'),
            'an inexact fetched fallback must not stop the search before the exact older match');
          assert.ok(!host.outerHTML.includes('cache_miss_pending'),
            'the pending placeholder must be replaced after predecessor resolution');

          const origResolve = context.resolveEntryForDetailAsync;
          context.resolveEntryForDetailAsync = async () => { throw new Error('records timeout'); };
          try {
            const failHost = pendingHost(0, false);
            loadEntries([unlabeledSeed, dashboardCur]);
            await upgrade(dashboardCur, pendingRoot(failHost));
            assert.ok(failHost.outerHTML.includes('data-reason="cache_miss_unknown"'),
              'a records-API reject must replace the pending card with the unknown state');
            assert.ok(!failHost.outerHTML.includes('cache_miss_pending'),
              'a failed fetch must not leave Analyzing the previous turn on screen');
          } finally {
            context.resolveEntryForDetailAsync = origResolve;
          }
        })().catch(err => { console.error(err); process.exit(1); });
        """
    )

    subprocess.run(["node", "-e", script, str(REPO_ROOT)], check=True, capture_output=True, text=True)
