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
          'sidebar.js',
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

          /* ── User input provenance ──
             Samples are verbatim openers taken from real local Claude sessions,
             since the whole point of the classifier is to recognize the exact
             templates a harness emits. TQ is a triple double-quote, built here
             so it does not terminate the Python string wrapping this script. */
          const TQ = '"'.repeat(3);
          const harnessSamples = [
            ['The user stepped away and is coming back. Recap in under 40 words.', 'recap'],
            ['[SYSTEM NOTIFICATION - NOT USER INPUT]\\nAutomated background event.', 'notification'],
            ['This session is being continued from a previous conversation that ran out of context.', 'compaction'],
            ['Perform a web search for the query: Anthropic pricing per million tokens', 'websearch'],
            ['CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.', 'subagent'],
            ['Briefly inform the user about the task result and perform any follow-up.', 'subagent'],
            ['[SUGGESTION MODE: Suggest what the user might naturally type next.]', 'suggestion'],
            ['<system-reminder>\\nAs you answer the user\\'s questions...', 'reminder'],
            ['[Request interrupted by user for tool use]', 'interrupt'],
            ['[Image: original 2880x1800, displayed at 2000x1250.]', 'attachment'],
          ];
          for (const [text, kind] of harnessSamples) {
            const got = classifyUserInputOrigin(text);
            assert.equal(got.origin, 'harness', 'expected harness for: ' + text.slice(0, 40));
            assert.equal(got.kind, kind, 'wrong kind for: ' + text.slice(0, 40));
          }

          const payloadSamples = [
            'diff --git a/a.js b/a.js\\nindex 000..111',
            '@@ -1,4 +1,9 @@\\n context',
            ':root {\\n  --bg: #f4f5f7;\\n}',
            'from __future__ import annotations\\n\\nimport json',
            '#!/usr/bin/env python3\\n' + TQ + 'Enforce coverage.' + TQ,
            TQ + 'Cross-client contract tests for the viewer.' + TQ,
            '/* ─── Renderers ─── */\\nfunction chatMessageContentToText(content) {',
            'function getPath(e) { return e.request?.path; }',
          ];
          for (const text of payloadSamples) {
            assert.equal(classifyUserInputOrigin(text).origin, 'payload',
              'expected payload for: ' + text.slice(0, 32));
          }

          /* Real human turns, including ones that talk *about* code. Prose that
             merely mentions a diff or a function must not be called payload. */
          const humanSamples = [
            '看一下PR 436是干什么的。',
            '读代码',
            '说中文',
            'Thanks, that is all.',
            '必要性很弱。必要性很弱是指这个需求没有意义？',
            'the diff --git output looked wrong, can you check?',
            'why does function getPath return undefined here?',
            'Perform the refactor we discussed.',
          ];
          for (const text of humanSamples) {
            assert.equal(classifyUserInputOrigin(text).origin, 'human',
              'expected human for: ' + text.slice(0, 32));
          }
          assert.equal(classifyUserInputOrigin('').origin, 'human');
          assert.equal(classifyUserInputOrigin(null).origin, 'human');

          /* Group titles name the human ask even when a harness injection sits
             earlier in the message list. */
          const mixedEntry = {
            request: {
              body: {
                messages: [
                  { role: 'user', content: [{ type: 'text', text: 'The user stepped away and is coming back. Recap.' }] },
                  { role: 'assistant', content: [{ type: 'text', text: 'ok' }] },
                  { role: 'user', content: [{ type: 'text', text: '把这个PR拆一下' }] },
                ],
              },
            },
          };
          const firstInfo = firstUserInputInfo(mixedEntry);
          assert.equal(firstInfo.userText, '把这个PR拆一下');
          assert.equal(firstInfo.origin, 'human');

          /* With nothing but injected text, the group still gets a title rather
             than going blank, and the origin says where it came from. */
          const injectedOnly = {
            request: {
              body: {
                messages: [
                  { role: 'user', content: [{ type: 'text', text: 'Perform a web search for the query: pricing' }] },
                ],
              },
            },
          };
          const injectedInfo = firstUserInputInfo(injectedOnly);
          assert.ok(injectedInfo.userText.startsWith('Perform a web search'));
          assert.equal(injectedInfo.origin, 'harness');

          /* latestUserInputInfo stops at the newest turn rather than falling
             back to older human messages in cumulative request history. */
          const cumulativeTurn2 = {
            request: {
              body: {
                messages: [
                  { role: 'user', content: [{ type: 'text', text: 'Human turn 1 prompt' }] },
                  { role: 'assistant', content: [{ type: 'text', text: 'ok' }] },
                  { role: 'user', content: [{ type: 'text', text: 'diff --git a/a.js b/a.js\\nindex 000..111' }] },
                ],
              },
            },
          };
          const latestInfo = latestUserInputInfo(cumulativeTurn2);
          assert.ok(latestInfo.userText.startsWith('diff --git'));
          assert.equal(latestInfo.origin, 'payload');
          assert.equal(latestInfo.userIndex, 2);

          /* Wrapped image placeholder with trailing human prompt */
          const imageWrapped = '<session>\\n[Image #1] what does this screenshot show?\\n</session>';
          const cleanedImg = cleanUserPromptText(imageWrapped);
          assert.equal(cleanedImg, 'what does this screenshot show?');
          assert.equal(classifyUserInputOrigin(cleanedImg).origin, 'human');

          /* Openers the cleaner blanks must classify as injected too. When the two
             disagree, the cleaner wins the title and the classifier wins the badge,
             so a message ends up blanked and labelled human prose. */
          const injectedOpeners = [
            '<environment_context>\\nrepo: claude-tap\\n</environment_context>',
            '<skills>\\nartifact-design\\n</skills>',
            '<user_information>\\nname: someone\\n</user_information>',
            '<additional_metadata>\\nrepo: claude-tap\\n</additional_metadata>',
            '# AGENTS.md instructions\\nRun ruff before committing.',
            '<INSTRUCTIONS>\\nBe concise.\\n</INSTRUCTIONS>',
            '# Files mentioned by the user:\\n- viewer.py',
          ];
          for (const opener of injectedOpeners) {
            assert.equal(cleanUserPromptText(opener), '', opener.slice(0, 24));
            assert.equal(classifyUserInputOrigin(opener).origin, 'harness', opener.slice(0, 24));
          }

          /* A tag that merely looks like a wrapper is still human prose: the set is
             matched on the whole tag name, not on a prefix of it. */
          assert.equal(classifyUserInputOrigin('<skillsets> are what I need').origin, 'human');

          /* An import is payload only when the statement ends where a source line
             would. A prefix-only match would badge prose about importing as pasted. */
          const pastedImports = [
            'import json\\nimport sys\\n',
            'import os.path as p\\n',
            'import json, sys\\n',
            'from collections import defaultdict\\n',
            'from typing import (\\n    Any,\\n)',
            'from claude_tap.viewer import *\\n',
          ];
          for (const text of pastedImports) {
            assert.equal(classifyUserInputOrigin(text).origin, 'payload', text.slice(0, 32));
          }
          const importProse = [
            'import pandas and plot the data',
            'from the import list, drop numpy',
            'import the trace into the viewer for me',
          ];
          for (const text of importProse) {
            assert.equal(classifyUserInputOrigin(text).origin, 'human', text.slice(0, 32));
          }

          /* async is a prefix, so a coroutine reads the same as its sync form.
             Spelling out only "async function" left a pasted "async def" winning
             a title over the question beside it. */
          for (const text of ['async def fetch_data():\\n    return 1\\n',
                              'def fetch_data():\\n    return 1\\n',
                              'async function fetchData() {\\n  return 1;\\n}\\n',
                              'function fetchData() {\\n  return 1;\\n}\\n']) {
            assert.equal(classifyUserInputOrigin(text).origin, 'payload', text.slice(0, 28));
          }

          /* Forms the cleaner blanks by pattern rather than by tag or prefix. Both
             sides read one shared list, so a blanked message still carries a badge
             instead of rendering as an empty human turn. */
          const blankedInjections = [
            ['Web page content:\\n\\nLorem ipsum from a fetched page.', 'context'],
            ['Page content: the rest of a scraped article', 'context'],
            ['网页内容：抓取到的正文', 'context'],
            ['[Image: source: /tmp/shot.png]', 'attachment'],
            ['[Image: original 2880x1800, displayed at 2000x1250.]', 'attachment'],
            ['<image_input>', 'attachment'],
          ];
          for (const [text, kind] of blankedInjections) {
            assert.equal(cleanUserPromptText(text), '', text.slice(0, 24));
            const got = classifyUserInputOrigin(text);
            assert.equal(got.origin, 'harness', text.slice(0, 24));
            assert.equal(got.kind, kind, text.slice(0, 24));
          }

          /* A known text type still falls through to the output key: the Python
             mirror accepts that shape, so reading only the text key here would
             change a session's grouping once it crosses LAZY_THRESHOLD. */
          assert.deepEqual(eligibleUserTextBlocks([{ type: 'input_text', output: 'from output key' }]),
            ['from output key']);

          /* ── An empty text field yields to output ── */
          const emptyTextBlock = { type: 'input_text', text: '',
            output: 'Perform a web search for the query: pricing' };
          assert.deepEqual(eligibleUserTextBlocks([emptyTextBlock]),
            ['Perform a web search for the query: pricing'],
            'an empty text field must not shadow the output that carries the text');
          assert.deepEqual(eligibleUserTextBlocks(emptyTextBlock),
            ['Perform a web search for the query: pricing'],
            'the same holds for a lone block outside a list');
          const emptyTextTitled = preferredUserTextForMessage({ role: 'user', content: [emptyTextBlock] });
          assert.equal(emptyTextTitled.origin, 'harness',
            'the extracted text is what gets classified');
          assert.deepEqual(eligibleUserTextBlocks([{ type: 'input_text', text: 'what I typed', output: 'ignored' }]),
            ['what I typed'], 'output is the fallback, not an override');

          /* The Responses rebuild in getMessages has to yield on blank text too.
             It rebuilds each block with the text key alone, so keeping the empty
             string there discarded the output before eligibleUserTextBlocks could
             reach it: below LAZY_THRESHOLD the turn lost the title that the Python
             metadata above it kept. */
          const rebuiltEmptyText = getMessages({
            input: [{ role: 'user', content: [{ type: 'input_text', text: '',
              output: 'Perform a web search for the query: pricing' }] }],
          });
          assert.deepEqual(eligibleUserTextBlocks(rebuiltEmptyText[0].content),
            ['Perform a web search for the query: pricing'],
            'the rebuilt block must carry the output text, not the empty string');
          const rebuiltRealText = getMessages({
            input: [{ role: 'user', content: [{ type: 'input_text', text: 'what I typed', output: 'ignored' }] }],
          });
          assert.deepEqual(eligibleUserTextBlocks(rebuiltRealText[0].content),
            ['what I typed'], 'a non-empty text still wins after the rebuild');

          /* ── Ordinary prose beginning with "Analyze" is human ── */
          /* A harness opener has to be unmistakable template text. This one was a
             plain English stem, so a human question matched it and was badged as
             harness-injected, yielding no group title and merging into the query
             before it. */
          const analyzeQuestion = 'Analyze if this message indicates fraud or a billing mistake';
          assert.equal(classifyUserInputOrigin(analyzeQuestion).origin, 'human',
            'a normal question must not be claimed by a template stem');
          assert.equal(preferredUserTextForMessage({
            role: 'user', content: [{ type: 'text', text: analyzeQuestion }],
          }).text, analyzeQuestion, 'and it still titles its own group');

          /* ── A base-less class declaration is payload ── */
          const pastedClass = 'class Foo:\\n    def run(self):\\n        return 1\\n';
          assert.equal(classifyUserInputOrigin(pastedClass).origin, 'payload',
            'class Foo: has no parenthesis, so the suffix set has to accept the colon');
          const classThenQuestion = preferredUserTextForMessage({
            role: 'user',
            content: [{ type: 'text', text: pastedClass }, { type: 'text', text: 'Why is this slow?' }],
          });
          assert.equal(classThenQuestion.text, 'Why is this slow?',
            'a pasted class must not out-title the question beside it');

          /* ── Prose with a colon after a keyword is not a declaration ──
             Mirror of test_prose_with_a_colon_after_a_keyword_is_not_a_declaration.
             A bare colon in the general suffix set matched English too, so plain
             questions were badged as pasted code and lost their title. */
          for (const typed of ['class action: can I join the settlement?',
                               'function calls: why are they slow?',
                               'def parse: what is this?',
                               'let me know: does the retry back off?',
                               'const rate: is that per request or per minute?']) {
            assert.equal(classifyUserInputOrigin(typed).origin, 'human', typed.slice(0, 32));
          }
          for (const pasted of ['class Foo:\\n    pass\\n',
                                'class Foo(Base):\\n    pass\\n',
                                'const value: string = "a";\\n',
                                'let count: number;\\n']) {
            assert.equal(classifyUserInputOrigin(pasted).origin, 'payload', pasted.slice(0, 32));
          }

          /* ── A JSON prompt array prefers the human item ──
             Mirror of test_a_json_prompt_array_prefers_the_human_item. Taking the
             first readable item let a leading injection title and badge the whole
             message while the question after it went unread. */
          const websearchItem = 'Perform a web search for the query: pricing';
          const arrayBoth = '[{"prompt":"' + websearchItem + '"},{"prompt":"What does that cost?"}]';
          assert.equal(cleanUserPromptText(arrayBoth), 'What does that cost?',
            'the human item wins over a leading injection');
          assert.equal(preferredUserTextForMessage({
            role: 'user', content: [{ type: 'text', text: arrayBoth }],
          }).origin, 'human', 'so the message is not filed under the harness');
          const arrayInjectionOnly = '[{"prompt":"' + websearchItem + '"}]';
          assert.equal(cleanUserPromptText(arrayInjectionOnly), websearchItem,
            'an array with no human item still yields its injection');
          assert.equal(classifyUserInputOrigin(cleanUserPromptText(arrayInjectionOnly)).origin, 'harness',
            'and still reads harness');
          assert.equal(cleanUserPromptText('[{"prompt":""},{"prompt":"Only one speaks"}]'), 'Only one speaks',
            'an item that cleans to nothing is skipped, not the end of the search');

          /* ── Command wrapper tags need a tag boundary ── */
          assert.equal(classifyUserInputOrigin('<local-command-caveats> are my own notes').origin, 'human',
            'a longer tag that starts the same way is the user\\'s own');
          assert.equal(classifyUserInputOrigin('<command-nameplate>Deploy</command-nameplate>').origin, 'human',
            'so is this one');
          assert.equal(classifyUserInputOrigin('<local-command-caveat>\\nOutput below\\n').origin, 'harness',
            'the real wrapper is still harness');
          assert.equal(classifyUserInputOrigin('<command-name status="ok">/cost</command-name>').origin, 'harness',
            'attributes on the real wrapper are fine');

          /* Provenance is read per block off the raw text, so an injection sharing
             its message with a tool result is still seen -- the joined message text
             would have started with the tool output and read as human prose. */
          const injectionBesideResult = {
            role: 'user',
            content: [
              { type: 'tool_result', tool_use_id: 'toolu_1', content: 'file contents here, ordinary prose' },
              { type: 'text', text: '<system-reminder>\\nBackground context.\\n</system-reminder>' },
            ],
          };
          const besideResult = preferredUserTextForMessage(injectionBesideResult);
          assert.equal(besideResult.origin, 'harness');
          assert.equal(besideResult.kind, 'reminder');
          /* Blank title on purpose: a group headed '[SUGGESTION MODE:' reads as
             noise, so the badge is kept while the title is left to an older turn. */
          assert.equal(besideResult.text, '');

          /* Human prose still wins over a payload block earlier in the same
             message, tool results notwithstanding. */
          const pastedThenProse = preferredUserTextForMessage({
            role: 'user',
            content: [
              { type: 'tool_result', tool_use_id: 'toolu_1', content: 'tool output' },
              { type: 'text', text: 'diff --git a/x b/x\\n+line' },
              { type: 'text', text: 'Does this look right?' },
            ],
          });
          assert.equal(pastedThenProse.text, 'Does this look right?');
          assert.equal(pastedThenProse.origin, 'human');

          /* An injected-only newest turn contributes no title, so the group keeps
             the human question it follows instead of being headed by the injection. */
          const injectedNewest = {
            request: {
              body: {
                messages: [
                  { role: 'user', content: [{ type: 'text', text: 'Split the pull request into two.' }] },
                  { role: 'assistant', content: [{ type: 'text', text: 'ok' }] },
                  { role: 'user', content: [{ type: 'text', text: '<system-reminder>\\nBackground.\\n</system-reminder>' }] },
                ],
              },
            },
          };
          const injectedNewestInfo = latestUserInputInfo(injectedNewest);
          assert.equal(injectedNewestInfo.userText, 'Split the pull request into two.');
          assert.equal(injectedNewestInfo.userIndex, 0);

          /* Serializing the origin is not enough on its own: buildStubEntry puts
             the title into body.messages, so both input scans find a message and
             return a fresh verdict on the bare title before ever reaching the
             stub fallback. A harness turn titled from a later pasted block came
             back as payload, which is the badge flip this key exists to stop. */
          const originStub = buildStubEntry({
            turn: 1,
            session_user_text: 'diff --git a/a b/a',
            session_user_origin: 'harness',
          }, 0);
          assert.equal(latestUserInputInfo(originStub).origin, 'harness',
            'a stub must keep the origin Python chose, not reclassify its title');
          assert.equal(firstUserInputInfo(originStub).origin, 'harness',
            'both scans read the stub the same way');

          /* Absent means human, so a stub without the key still classifies. */
          const unmarkedStub = buildStubEntry({
            turn: 2,
            session_user_text: 'diff --git a/a b/a',
          }, 1);
          assert.equal(latestUserInputInfo(unmarkedStub).origin, 'payload',
            'without a stored origin the title is all there is to read');

          /* ── Titles hidden by the CSS clamp, not by the snippet ── */

          function fakeHeader(nameMetrics) {
            const name = Object.assign(element(), nameMetrics);
            const header = element();
            header.querySelector = sel => (sel === '.group-name' ? name : null);
            header.getBoundingClientRect = () => ({ top: 0, right: 300 });
            return header;
          }

          /* Two truncation mechanisms disagree: sessionTextSnippet leaves a
             48-character title whole, while the two-line CSS clamp with
             overflow-wrap anywhere still cuts it once the badge and counters
             take their share of the sidebar. Binding on the snippet's "..."
             therefore left exactly the clipped titles with no way to read them. */
          const clampedText = 'src/very/long/unbroken/path/name/module_alpha.py';
          const clampedLabel = sessionTextSnippet(clampedText, 48);
          assert.ok(!clampedLabel.endsWith('...'),
            'a 48-character title earns no snippet ellipsis');

          const roomy = fakeHeader({ scrollHeight: 36, clientHeight: 36, scrollWidth: 200, clientWidth: 200 });
          bindSessionInputTooltip(roomy, clampedText, clampedLabel);
          sessionTooltip().textContent = '';
          showSessionTooltip(roomy);
          assert.equal(sessionTooltip().textContent, '',
            'a fully visible title must not raise a tooltip over itself');

          const clipped = fakeHeader({ scrollHeight: 54, clientHeight: 36, scrollWidth: 200, clientWidth: 200 });
          bindSessionInputTooltip(clipped, clampedText, clampedLabel);
          assert.equal(clipped.dataset.fullUserInput, clampedText,
            'the full text is attached whatever the snippet did');
          assert.equal(clipped.tabIndex, 0, 'a clipped title stays keyboard reachable');
          showSessionTooltip(clipped);
          assert.equal(sessionTooltip().textContent, clampedText,
            'a title cut by the clamp must still reveal its full text');

          /* A title the snippet did shorten keeps working even where the header
             cannot be measured, which is how the tooltip behaved before. */
          const ellipsised = element();
          ellipsised.getBoundingClientRect = () => ({ top: 0, right: 300 });
          bindSessionInputTooltip(ellipsised, 'a'.repeat(80), sessionTextSnippet('a'.repeat(80), 48));
          sessionTooltip().textContent = '';
          showSessionTooltip(ellipsised);
          assert.equal(sessionTooltip().textContent, 'a'.repeat(80),
            'a snippet-truncated title needs no layout measurement');

          /* Kind slugs go through the i18n table, and an unknown slug passes
             through rather than rendering as a missing-key string. */
          assert.equal(kindLabel('recap'), 'recap');
          assert.equal(kindLabel(''), '');
          assert.equal(kindLabel('not-a-kind'), 'not-a-kind');

          /* ── Mirror parity: the same input must classify the same way here and
                in viewer.py, or a paste changes its badge, title and grouping as
                the capture crosses LAZY_THRESHOLD. ── */

          /* A non-ASCII identifier is still an identifier. The JS word-class
             escape is ASCII-only while Python's is Unicode-aware, so the patterns
             spell the class out; reverting either side makes this read as prose. */
          const unicodePayload = [
            'def 处理():\\n    pass',
            'function 计算(x) {\\n  return x;\\n}',
            'const λ = 1',
            'let Ünïcode = {',
            'import 模块\\n',
            'from 包 import 东西\\n',
            'import 包.子模块 as 别名\\n',
          ];
          for (const text of unicodePayload) {
            assert.equal(classifyUserInputOrigin(text).origin, 'payload',
              'non-ASCII identifier must still be payload: ' + text.slice(0, 24));
          }

          /* Prose that merely mentions such code stays human: the payload
             patterns still require a real declaration head. */
          assert.equal(classifyUserInputOrigin('def 处理 should be renamed?').origin, 'human');
          assert.equal(classifyUserInputOrigin('把 import 模块 改成绝对导入').origin, 'human');

          /* Non-ASCII digits are the same trap in reverse: the Python digit
             escape matches them and the JS one does not, so both sides pin
             ASCII digits explicitly. */
          assert.equal(classifyUserInputOrigin('@@ -12,3 +12,4 @@\\n ctx').origin, 'payload');
          assert.equal(classifyUserInputOrigin('   1\\tfirst line').origin, 'payload');
          assert.equal(classifyUserInputOrigin('@@ -١٢ لا يوجد').origin, 'human');
          assert.equal(classifyUserInputOrigin('  ١\\tArabic-Indic digit prose').origin, 'human');

          /* A badge-only first block must not lock in an empty title. The turn
             would otherwise render untitled and merge into the group before it,
             hiding a pasted diff or a readable harness request. */
          const emptyThenPayload = preferredUserTextForMessage({
            role: 'user',
            content: [
              { type: 'text', text: '<system-reminder>\\nBackground.\\n</system-reminder>' },
              { type: 'text', text: 'diff --git a/x b/x\\n+line' },
            ],
          });
          assert.equal(emptyThenPayload.text, 'diff --git a/x b/x\\n+line',
            'a later nonempty block must supply the title');
          assert.equal(emptyThenPayload.origin, 'harness',
            'the first block still owns the provenance');
          assert.equal(emptyThenPayload.kind, 'reminder');

          /* That provenance has to travel with the title rather than be derived
             again from it. This is the same turn: its first block is a blanked
             harness reminder and its title comes from the later pasted diff, so
             classifying the title alone reads the diff as payload and the group
             header contradicts the detail badge above the very same turn. */
          const originCarryEntry = {
            request_id: 'req_origin_carry',
            request: { path: '/v1/messages', method: 'POST', body: { messages: [{
              role: 'user',
              content: [
                { type: 'text', text: '<system-reminder>\\nBackground.\\n</system-reminder>' },
                { type: 'text', text: 'diff --git a/x b/x\\n+line' },
              ],
            }] } },
          };
          const carriedKey = sessionKeyForEntry(originCarryEntry, null);
          assert.equal(carriedKey.userText, 'diff --git a/x b/x\\n+line');
          assert.equal(carriedKey.origin, 'harness',
            'sessionKeyForEntry must carry the origin the detail badge shows');
          assert.equal(classifyUserInputOrigin(carriedKey.userText).origin, 'payload',
            'deriving the origin from the title alone is the disagreement being guarded');
          const carriedGroups = buildSessionGroups([{ entry: originCarryEntry }]);
          assert.equal(carriedGroups.length, 1);
          assert.equal(carriedGroups[0].origin, 'harness',
            'the group carries the origin through to the header');

          /* Human prose and a plain pasted turn keep classifying as before, so
             the threading did not pin every group to one origin. */
          for (const [text, expected] of [
            ['please fix the parser', 'human'],
            ['diff --git a/y b/y\\n+one', 'payload'],
          ]) {
            const entry = {
              request_id: 'req_origin_' + expected,
              request: { path: '/v1/messages', method: 'POST', body: { messages: [{
                role: 'user',
                content: [{ type: 'text', text }],
              }] } },
            };
            assert.equal(sessionKeyForEntry(entry, null).origin, expected, text);
            assert.equal(buildSessionGroups([{ entry }])[0].origin, expected, text);
          }

          /* An untitled entry opening a group must not fix the group's origin:
             the title and its provenance arrive together from whichever entry
             supplies the title. */
          const untitledFirst = {
            request_id: 'req_origin_untitled',
            request: { path: '/v1/messages', method: 'POST', body: { messages: [{
              role: 'user',
              content: [{ type: 'text', text: '<image_input>' }],
            }] } },
          };
          const lateTitled = {
            request_id: 'req_origin_late',
            request: { path: '/v1/messages', method: 'POST', body: { messages: [{
              role: 'user',
              content: [{ type: 'text', text: 'diff --git a/z b/z\\n+late' }],
            }] } },
          };
          const lateGroups = buildSessionGroups([{ entry: untitledFirst }, { entry: lateTitled }]);
          const titledGroup = lateGroups.find(group => group.userText);
          assert.ok(titledGroup, 'one group must carry the later title');
          assert.equal(titledGroup.origin, 'payload',
            'the origin follows the entry that supplied the title');

          /* Both blocks blank leaves the title blank, as before. */
          const bothBlank = preferredUserTextForMessage({
            role: 'user',
            content: [
              { type: 'text', text: '<system-reminder>\\nOne.\\n</system-reminder>' },
              { type: 'text', text: '<image_input>' },
            ],
          });
          assert.equal(bothBlank.text, '');
          assert.equal(bothBlank.origin, 'harness');
          assert.equal(bothBlank.kind, 'reminder');

          /* A nonempty first block is not displaced by a later one. */
          const firstWins = preferredUserTextForMessage({
            role: 'user',
            content: [
              { type: 'text', text: 'diff --git a/a b/a\\n+one' },
              { type: 'text', text: 'diff --git a/b b/b\\n+two' },
            ],
          });
          assert.equal(firstWins.text, 'diff --git a/a b/a\\n+one');
          assert.equal(firstWins.origin, 'payload');

          /* Responses normalization copies the output fallback onto text.
             Keeping output as a separate key titled the sidebar but left
             hasDisplayContent / renderContent empty, so renderMessages dropped
             the whole user turn. */
          const outputOnly = getMessages({
            input: [{ role: 'user', content: [{ type: 'input_text', output: 'Reconstruct me.' }] }],
          });
          assert.equal(outputOnly.length, 1);
          assert.deepEqual(eligibleUserTextBlocks(outputOnly[0].content), ['Reconstruct me.'],
            'output-keyed text must survive normalization');
          assert.equal(hasDisplayContent(outputOnly[0].content), true,
            'normalized output-backed text must count as display content');
          const outputRendered = renderMessages(outputOnly);
          assert.ok(outputRendered.includes('Reconstruct me.'),
            'renderMessages must keep an output-backed user turn');

          /* Raw blocks that never went through getMessages still have to render:
             hasDisplayContent and renderContent read output when text is
             absent, matching eligibleUserTextBlocks. */
          const rawOutput = [{ type: 'input_text', output: 'Perform a web search for the query: token pricing' }];
          assert.equal(hasDisplayContent(rawOutput), true);
          const rawRendered = renderMessages([{ role: 'user', content: rawOutput }]);
          assert.ok(rawRendered.includes('Perform a web search for the query: token pricing'));
          assert.ok(rawRendered.includes('origin-harness'),
            'output-backed harness text must keep its provenance badge');

          /* The text key still wins when it says something. An empty one does
             not: these captures write text as an empty string to mean the
             readable text sits under output, so both mirrors yield on blank
             text and the rebuild has to agree. This block used to assert the
             opposite, which is how the rebuild came to drop the only readable
             content an injected turn had. */
          const bothKeys = getMessages({
            input: [{
              role: 'user',
              content: [
                { type: 'input_text', text: 'From text.', output: 'From output.' },
                { type: 'input_text', text: '', output: 'From the empty one.' },
              ],
            }],
          });
          assert.deepEqual(eligibleUserTextBlocks(bothKeys[0].content),
            ['From text.', 'From the empty one.'],
            'a real text wins, a blank one yields to output');

          /* The render path has to yield on blank text the same way. It read
             text first merely because it was a string, so a raw block that
             never went through getMessages rendered as an empty frame while the
             sidebar titled the turn from output. */
          const blankText = [{ type: 'input_text', text: '', output: 'Perform a web search for pricing' }];
          assert.equal(hasDisplayContent(blankText), true,
            'blank text beside a populated output is still displayable');
          const blankRendered = renderMessages([{ role: 'user', content: blankText }]);
          assert.ok(blankRendered.includes('Perform a web search for pricing'),
            'renderContent must yield on blank text like the sidebar does');

          /* A CLI can put an injected block and the user's own prose in one
             message. The header badge names the block the sidebar titled from --
             the human prose -- so the injection needs its own badge or it renders
             under a bare "user" label. */
          const mixed = [{
            role: 'user',
            content: [
              { type: 'text', text: '<system-reminder>Background.</system-reminder>' },
              { type: 'text', text: 'Fix the parser' },
            ],
          }];
          assert.equal(preferredUserTextForMessage(mixed[0]).origin, 'human',
            'the human block still titles the group');
          const mixedRendered = renderMessages(mixed);
          assert.equal((mixedRendered.match(/block-origin/g) || []).length, 1,
            'exactly the disagreeing block carries a badge');
          assert.ok(mixedRendered.includes('block-origin origin-harness'),
            'the injected block is named as harness');
          assert.equal((mixedRendered.match(/msg-origin/g) || []).length, 0,
            'a human-titled message keeps its header unqualified');

          /* When every block agrees, one header badge is enough. */
          const allHarness = [{
            role: 'user',
            content: [
              { type: 'text', text: '<system-reminder>One.</system-reminder>' },
              { type: 'text', text: '<system-reminder>Two.</system-reminder>' },
            ],
          }];
          const harnessRendered = renderMessages(allHarness);
          assert.equal((harnessRendered.match(/block-origin/g) || []).length, 0,
            'agreeing blocks must not be labelled twice');
          assert.ok(harnessRendered.includes('msg-origin origin-harness'));

          /* The per-block pass has to clean before classifying, exactly as the
             message-level verdict does. A JSON-wrapped injection classified raw
             reads as ordinary prose -- the braces are not a known prefix -- so
             the block came back human, matched the header, and the injection
             rendered under a bare "user" label. */
          const wrappedMixed = [{
            role: 'user',
            content: [
              { type: 'text', text: '{"prompt":"Perform a web search for the query: pricing"}' },
              { type: 'text', text: 'What does that cost?' },
            ],
          }];
          assert.equal(preferredUserTextForMessage(wrappedMixed[0]).origin, 'human',
            'the prose still titles the turn');
          const wrappedRendered = renderMessages(wrappedMixed);
          assert.equal((wrappedRendered.match(/block-origin/g) || []).length, 1,
            'the JSON-wrapped injection carries its own badge');
          assert.ok(wrappedRendered.includes('block-origin origin-harness'),
            'unwrapping reveals the harness template the raw braces hid');

          /* Cleaning that blanks a block must not cost it its provenance: the
             empty string classifies human, so fall back to the raw text. */
          const blankedMixed = [{
            role: 'user',
            content: [
              { type: 'text', text: '<system-reminder>Background.</system-reminder>' },
              { type: 'text', text: 'Fix the parser' },
            ],
          }];
          assert.ok(renderMessages(blankedMixed).includes('block-origin origin-harness'),
            'an injection cleaning empties keeps its badge');

          /* Mirror of test_a_bom_only_block_is_skipped_like_javascript_skips_it.
             String.trim() drops U+FEFF and Python's strip() does not, so this is
             the shape where the two mirrors silently parted: the browser skipped
             the block, Python kept it as an empty human fallback, and the diff
             behind it inherited that origin above LAZY_THRESHOLD. */
          const bomLead = {
            role: 'user',
            content: [
              { type: 'text', text: '\uFEFF' },
              { type: 'text', text: 'diff --git a/a b/a\\n+line' },
            ],
          };
          assert.deepEqual(eligibleUserTextBlocks(bomLead.content), ['diff --git a/a b/a\\n+line'],
            'a BOM-only block is not an eligible block');
          assert.equal(preferredUserTextForMessage(bomLead).origin, 'payload',
            'the diff decides the origin, unblocked by the BOM');

          /* Mirror of test_a_bom_only_text_field_still_yields_to_output. A
             Responses block puts its readable text under output when text is
             blank, and a BOM counts as blank only where trim removes it: Python
             returned the BOM, dropped the block, and lost the injection. */
          const bomOverOutput = { type: 'input_text', text: '\uFEFF', output: 'Perform a web search for the query: pricing' };
          assert.equal(blockInputText(bomOverOutput), 'Perform a web search for the query: pricing',
            'a BOM-only text field yields to the output beside it');
          assert.equal(preferredUserTextForMessage({ role: 'user', content: [bomOverOutput] }).origin, 'harness',
            'the output decides the origin, unblocked by the BOM');

          /* JSON prompt wrappers unwrap before classification, so a lazy
             Python title and the browser title stay the same prompt. */
          const jsonPrompt = '{"prompt":"Perform a web search for the query: token pricing"}';
          assert.equal(cleanUserPromptText(jsonPrompt),
            'Perform a web search for the query: token pricing');
          assert.equal(classifyUserInputOrigin(cleanUserPromptText(jsonPrompt)).origin, 'harness');
          const jsonArray = '[{"prompt":"Perform a web search for the query: token pricing"}]';
          assert.equal(cleanUserPromptText(jsonArray),
            'Perform a web search for the query: token pricing');

          /* A JSON wrapper whose known-type block stores the prompt in
             output must unwrap the same way the Python mirror does. */
          const wrappedOutput = {
            content: [{ type: 'input_text', output: 'Perform a web search for the query: pricing' }],
          };
          assert.equal(
            naturalTextForSessionContent(wrappedOutput.content),
            'Perform a web search for the query: pricing',
          );
          assert.equal(
            naturalTextFromPromptPayload(wrappedOutput),
            'Perform a web search for the query: pricing',
          );

          const bomImport = '\uFEFFimport os';
          assert.equal(classifyUserInputOrigin(bomImport).origin, 'payload',
            'a BOM must not hide a pasted import from classification');
          assert.equal(cleanUserPromptText(bomImport), 'import os');

        `, context);
        """
    )

    try:
        subprocess.run(["node", "-e", script, str(REPO_ROOT)], check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as err:
        raise AssertionError(f"Node test script failed:\nSTDOUT:\n{err.stdout}\nSTDERR:\n{err.stderr}") from err
