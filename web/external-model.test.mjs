/* external-model 纯函数单测：node web/external-model.test.mjs
 *
 * 只测地址校验 normalizeModelUrl，零 DOM、零请求。重点钉两件事：
 * 合法输入（https 链接、/static/ 站内路径）照常通过；协议相对 URL 与反斜杠
 * 这类曾被 startsWith("/") 放行、实际会解析到外部主机的形态必须被拒。 */
import test from "node:test";
import assert from "node:assert/strict";

import { normalizeModelUrl } from "./external-model.js";

test("空与非字符串输入被拒，且给出理由", () => {
  for (const raw of ["", "   ", null, undefined, 42, {}, []]) {
    const r = normalizeModelUrl(raw);
    assert.equal(r.ok, false, String(raw));
    assert.equal(typeof r.reason, "string");
    assert.ok(r.reason.length > 0);
  }
  // 空串走专用提示而非兜底文案——删掉专门的空值护栏这条会红。
  assert.equal(normalizeModelUrl("").reason, "请先填写 .vrm 模型地址。");
  assert.equal(normalizeModelUrl("   ").reason, "请先填写 .vrm 模型地址。");
});

test("超长地址被拒（https 与站内路径都算）", () => {
  assert.equal(normalizeModelUrl("https://ok.com/" + "a".repeat(3000)).ok, false);
  assert.equal(normalizeModelUrl("/static/" + "a".repeat(3000)).ok, false);
});

test("合法 https 链接通过；畸形与不安全 scheme 被拒", () => {
  assert.deepEqual(normalizeModelUrl("  https://ok.com/a.vrm  "),
                   { ok: true, url: "https://ok.com/a.vrm" });
  assert.equal(normalizeModelUrl("HTTPS://OK.COM/a.vrm").ok, true);  // scheme 大小写不敏感
  assert.equal(normalizeModelUrl("https://").ok, false);             // 无法解析
  assert.equal(normalizeModelUrl("http://insecure.com/a.vrm").ok, false);
  assert.equal(normalizeModelUrl("javascript:alert(1)").ok, false);
});

test("站内 /static/ 路径通过", () => {
  assert.deepEqual(normalizeModelUrl("/static/models/a.vrm"),
                   { ok: true, url: "/static/models/a.vrm" });
});

test("协议相对 URL 与反斜杠被拒——它们不是站内路径", () => {
  // //host 与 /\host 都会被浏览器按当前协议解析成外部主机，绕过 https 限定。
  for (const raw of ["//evil.com/x.vrm", "/\\evil.com/x.vrm", "/\\/evil.com/x.vrm",
                     "/static/\\evil.com/x.vrm", "/models/a.vrm", "/", "/x.vrm"]) {
    const r = normalizeModelUrl(raw);
    assert.equal(r.ok, false, raw);
    assert.ok(r.reason.includes("/static/"), raw);  // 文案指向受支持的形态
  }
});
