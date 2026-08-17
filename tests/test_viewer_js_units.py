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

          /* ── Profiler: Model pricing and cost calculation ── */
          const sonnetPricing = getModelPricing('claude-3-7-sonnet-20250219');
          assert.ok(sonnetPricing);
          assert.equal(sonnetPricing.input, 3.0);
          assert.equal(sonnetPricing.output, 15.0);
          assert.equal(sonnetPricing.cacheRead, 0.3);

          /* Only Anthropic models are priced.  Other providers tier by context
             length or span a wide range under one brand name, so a match would
             misprice rather than inform -- they must return null, not a guess. */
          assert.equal(getModelPricing('gpt-4o'), null);
          assert.equal(getModelPricing('deepseek-chat'), null);
          assert.equal(getModelPricing('gemini-2.5-flash'), null);
          assert.equal(getModelPricing('kimi-k2.7-code'), null);
          assert.equal(getModelPricing(''), null);
          assert.equal(getModelPricing(null), null);

          /* No pricing means no cost, rather than a zero that reads as free. */
          assert.equal(calculateEntryCost({
            request: { body: { model: 'gpt-4o' } },
            response: { body: { usage: { input_tokens: 1000, output_tokens: 200 } } },
          }), null);

          /* The models actually in use must resolve, and the newer -4-5/-5 names
             must not be captured by the broader patterns for older families. */
          const currentModelRates = {
            'claude-fable-5': [10.0, 50.0],
            'claude-opus-5': [5.0, 25.0],
            'claude-sonnet-5': [2.0, 10.0],
            'claude-haiku-4-5-20251001': [1.0, 5.0],
            'claude-opus-4-5-20251101': [5.0, 25.0],
            'claude-opus-4-1-20250805': [15.0, 75.0],
            'claude-sonnet-4-20250514': [3.0, 15.0],
          };
          for (const [model, rates] of Object.entries(currentModelRates)) {
            const input = rates[0], output = rates[1];
            const p = getModelPricing(model);
            assert.ok(p, 'expected pricing for ' + model);
            assert.equal(p.input, input, 'input rate for ' + model);
            assert.equal(p.output, output, 'output rate for ' + model);
            /* Anthropic cache rates are fixed multiples of the input rate. */
            assert.ok(Math.abs(p.cacheRead - input * 0.1) < 1e-9, 'cacheRead for ' + model);
            assert.ok(Math.abs(p.cacheWrite - input * 1.25) < 1e-9, 'cacheWrite for ' + model);
          }

          /* PRICING_AS_OF is shown to the reader, so it must stay an ISO date. */
          const asOfParts = String(PRICING_AS_OF).split('-');
          assert.equal(asOfParts.length, 3, 'PRICING_AS_OF must be YYYY-MM-DD');
          assert.deepEqual(asOfParts.map(part => part.length), [4, 2, 2]);
          asOfParts.forEach(part => assert.ok(Number.isInteger(Number(part))));

          assert.equal(formatCostUsd(0), '$0.00');
          assert.equal(formatCostUsd(0.00003), '<$0.0001');
          assert.equal(formatCostUsd(0.0003), '$0.0003');
          assert.equal(formatCostUsd(0.0052), '$0.005');
          assert.equal(formatCostUsd(0.1234), '$0.12');

          const sampleClaudeEntry = {
            request: { body: { model: 'claude-3-7-sonnet-20250219' } },
            response: {
              body: {
                usage: {
                  input_tokens: 1000,
                  output_tokens: 200,
                  cache_read_input_tokens: 5000,
                  cache_creation_input_tokens: 500,
                },
              },
            },
          };
          const costInfo = calculateEntryCost(sampleClaudeEntry);
          assert.ok(costInfo);
          // (1000*3 + 200*15 + 5000*0.3 + 500*3.75) / 1e6 = (3000 + 3000 + 1500 + 1875) / 1e6 = $0.009375
          assert.ok(Math.abs(costInfo.cost - 0.009375) < 1e-6);
          /* Uncached, every input token bills at the full rate:
             ((1000+5000+500)*3 + 200*15) / 1e6 = $0.0225, so caching saved
             0.0225 - 0.009375 = $0.013125. */
          assert.ok(Math.abs(costInfo.saved - 0.013125) < 1e-6);

          /* Cache creation has a 25% write premium (1.25x), so a write-only turn
             yields a negative saving (it cost more than uncached). */
          const writeOnlyInfo = calculateEntryCost({
            request: { body: { model: 'claude-3-7-sonnet-20250219' } },
            response: { body: { usage: { input_tokens: 0, output_tokens: 0, cache_creation_input_tokens: 1000 } } },
          });
          assert.ok(writeOnlyInfo.saved < 0, 'write-only turn must have negative savings');
          assert.ok(Math.abs(writeOnlyInfo.saved - (-0.00075)) < 1e-6);

          /* With no cache activity there is nothing to have saved, and the header
             hides the Saved stat rather than showing $0.00. */
          const uncachedInfo = calculateEntryCost({
            request: { body: { model: 'claude-3-7-sonnet-20250219' } },
            response: { body: { usage: { input_tokens: 1000, output_tokens: 200 } } },
          });
          assert.ok(Math.abs(uncachedInfo.cost - 0.006) < 1e-6); // (1000*3 + 200*15) / 1e6
          assert.equal(uncachedInfo.saved, 0);

          /* ── Profiler: Tool bloat detection ── */
          const smallToolBlock = { type: 'tool_result', tool_use_id: 'tool_1', content: 'short output' };
          assert.equal(toolResultBloatInfo(smallToolBlock), null);

          const largeToolBlock = { type: 'tool_result', tool_use_id: 'tool_2', content: 'x'.repeat(25000) };
          const largeInfo = toolResultBloatInfo(largeToolBlock);
          assert.ok(largeInfo);
          assert.equal(largeInfo.charCount, 25000);
          assert.ok(parseFloat(largeInfo.sizeKB) >= 24);

          const imageToolBlock = {
            type: 'tool_result',
            tool_use_id: 'tool_img',
            content: [{ type: 'image', source: { type: 'base64', data: 'x'.repeat(50000) } }],
          };
          assert.equal(toolResultBloatInfo(imageToolBlock), null, 'image blocks must not count as text bloat');

          /* The threshold is inclusive: exactly TOOL_BLOAT_MIN_CHARS counts as bloat,
             one character less does not. */
          const atThreshold = { type: 'tool_result', content: 'x'.repeat(TOOL_BLOAT_MIN_CHARS) };
          const belowThreshold = { type: 'tool_result', content: 'x'.repeat(TOOL_BLOAT_MIN_CHARS - 1) };
          const edgeInfo = toolResultBloatInfo(atThreshold);
          assert.ok(edgeInfo);
          assert.equal(edgeInfo.charCount, TOOL_BLOAT_MIN_CHARS);
          assert.equal(edgeInfo.estTokens, Math.round(TOOL_BLOAT_MIN_CHARS / CHARS_PER_TOKEN));
          assert.equal(toolResultBloatInfo(belowThreshold), null);

          /* Non-tool_result blocks are never flagged, however large. */
          assert.equal(toolResultBloatInfo({ type: 'text', text: 'x'.repeat(50000) }), null);
          assert.equal(toolResultBloatInfo(null), null);

          /* Sizing must read every shape a tool result can take, not just strings:
             a block whose content is a list of parts is measured across the parts. */
          const listContentBlock = {
            type: 'tool_result',
            content: [{ type: 'text', text: 'y'.repeat(6000) }, { type: 'text', text: 'z'.repeat(6000) }],
          };
          const listInfo = toolResultBloatInfo(listContentBlock);
          assert.ok(listInfo, 'list-shaped content must be measured');
          assert.equal(listInfo.charCount, 12001); // both parts plus the joining newline

          const bloatedEntry = {
            request: {
              body: {
                messages: [
                  { role: 'user', content: [{ type: 'text', text: 'run tool' }] },
                  { role: 'user', content: [largeToolBlock] },
                ],
              },
            },
          };
          const bloatList = detectEntryToolBloat(bloatedEntry);
          assert.equal(bloatList.length, 1);
          assert.equal(bloatList[0].charCount, 25000);

          /* ── Direct DOM: Cost and Saved in applyFilter ── */
          entries = [
            makeUsageEntry({
              input_tokens: 1000, output_tokens: 200,
              cache_read_input_tokens: 5000,
            }),
          ];
          entries[0].request.body.model = 'claude-3-7-sonnet';
          applyFilter();
          assert.equal(_statEls['stat-cost-group'].style.display, 'flex');
          assert.equal(_statEls['stat-saved-group'].style.display, 'flex');
          /* Every turn is priced here, so the tooltip carries the rate date alone. */
          assert.ok(_statEls['stat-cost-group'].title.indexOf('cost_unpriced_turns') === -1,
            'all-priced session must not claim turns were excluded');

          /* Mixing a priced and an unpriced model: the total covers only the priced
             turn, and the tooltip has to say so instead of passing a partial sum off
             as the session cost. */
          const unpricedEntry = makeUsageEntry({ input_tokens: 9000, output_tokens: 800 });
          unpricedEntry.request.body.model = 'gpt-4o';
          entries = [entries[0], unpricedEntry];
          applyFilter();
          assert.equal(_statEls['stat-cost-group'].style.display, 'flex',
            'a priced turn still yields a cost even alongside an unpriced one');
          assert.ok(_statEls['stat-cost-group'].title.indexOf('cost_unpriced_turns') !== -1,
            'mixed session must disclose the excluded turns');

          /* No turn is priced, so there is no partial sum to disclose -- the stat
             hides rather than showing $0.00, which would read as free. */
          entries = [unpricedEntry];
          applyFilter();
          assert.equal(_statEls['stat-cost-group'].style.display, 'none');
          assert.equal(_statEls['stat-saved-group'].style.display, 'none');
        `, context);
        """
    )

    subprocess.run(["node", "-e", script, str(REPO_ROOT)], check=True, capture_output=True, text=True)
