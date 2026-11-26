(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
  var traderId = "", timer = null;

  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]; }); }
  function cls(n) { return n >= 0 ? "gain" : "loss"; }
  function arrow(n) { return n >= 0 ? "▲ +" : "▼ −"; }
  function money(n) { return arrow(n) + usd.format(Math.abs(n)); }
  function pct(n, d) { return arrow(n) + Math.abs(n).toFixed(d == null ? 2 : d) + "%"; }
  function price(n) { return usd.format(n).replace(/\.00$/, n < 1000 ? ".00" : ""); }
  function num(n) { return Number(n).toLocaleString("en-US", { maximumFractionDigits: 6 }); }

  function api(path, opts) {
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.detail || ("Request failed (" + r.status + ")"));
        return j;
      });
    });
  }
  function showError(m) { var e = $("error"); e.hidden = !m; e.textContent = m || ""; }
  function card(l, v, c, sub) {
    return "<div class='card'><div class='label'>" + l + "</div><div class='big " + (c || "") + "'>" + v + "</div>" +
      (sub ? "<div class='" + c + "'>" + sub + "</div>" : "") + "</div>";
  }
  function row(cols, none, n) { return cols.length ? cols.join("") : "<tr><td colspan='" + n + "' class='empty'>" + none + "</td></tr>"; }

  function refresh() {
    var id = encodeURIComponent(traderId);
    Promise.all([
      api("/traders/" + id + "/portfolio"),
      api("/traders/" + id + "/orders?limit=15"),
      api("/predictions?limit=20"),
      api("/accuracy"),
      api("/leaderboard"),
      api("/health"),
      api("/prices")
    ]).then(function (r) {
      showError("");
      draw(r[0], r[1], r[2], r[3], r[4], r[5], r[6]);
    }).catch(function (e) {
      showError("Could not reach the server: " + e.message + ". Make sure arena_server.py is running.");
    });
  }

  function draw(pf, od, pr, ac, lb, hl, px) {
    $("updated").textContent = "Last updated: " + new Date().toLocaleTimeString() + " · refreshes every 15 seconds";
    var open = hl.us_market_open;
    $("mkt").className = "pill " + (open ? "gain" : "");
    $("mktText").textContent = open ? "US stock market open" : "US stock market closed (crypto trades 24/7)";

    var pnl = pf.total_value - pf.starting_cash;
    $("cards").innerHTML =
      card("Total value", usd.format(pf.total_value), "") +
      card("Overall profit / loss", money(pnl), cls(pnl), pct(pf.pnl_pct)) +
      card("Cash available", usd.format(pf.cash), "") +
      card("Holdings", String(pf.positions.length), "", usd.format(pf.positions_value) + " invested");

    $("positions").innerHTML = row(pf.positions.map(function (p) {
      return "<tr><th scope='row'>" + esc(p.symbol) + "</th><td>" + num(p.qty) + "</td><td>" + usd.format(p.avg_cost) +
        "</td><td>" + usd.format(p.current_price) + "</td><td>" + usd.format(p.value) +
        "</td><td class='" + cls(p.unrealized_pnl) + "'><b>" + money(p.unrealized_pnl) + " (" + pct(p.unrealized_pnl_pct) + ")</b></td></tr>";
    }), "You don't hold anything yet. Use “Place a practice trade” above.", 6);

    $("orders").innerHTML = row(od.orders.map(function (o) {
      var buy = o.side === "BUY";
      return "<tr><td>" + new Date(o.timestamp).toLocaleString([], { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) +
        "</td><th scope='row'>" + esc(o.symbol) + "</th><td class='" + (buy ? "gain" : "loss") + "'><b>" + (buy ? "▲ Buy" : "▼ Sell") +
        "</b></td><td>" + num(o.qty) + "</td><td>" + usd.format(o.price) + "</td><td>" + usd.format(o.notional) + "</td></tr>";
    }), "No trades yet.", 6);

    var acc = ac.overall || {};
    $("acc").textContent = acc.n ? "Past accuracy: the bot guessed the direction correctly " + Math.round(acc.directional_accuracy * 100) + "% of the time (" + acc.n + " checked suggestions)." : "Accuracy will appear once the bot has checked some earlier suggestions.";
    var label = { BUY: ["▲ Buy", "gain"], SELL: ["▼ Sell", "loss"], HOLD: ["● Hold", ""] };
    $("preds").innerHTML = row(pr.predictions.map(function (p) {
      var l = label[p.signal], r = p.predicted_return * 100;
      return "<tr><th scope='row'>" + esc(p.symbol) + "</th><td class='" + l[1] + "'><b>" + l[0] + "</b></td><td class='" + cls(r) + "'>" + pct(r, 3) +
        "</td><td>" + Math.round(p.confidence * 100) + "%</td></tr>";
    }), "No suggestions yet. Make sure the bot (python -m src) has been running for a few minutes.", 4);

    $("board").innerHTML = row(lb.leaderboard.map(function (b) {
      return "<tr><td>" + b.rank + "</td><th scope='row'>" + esc(b.trader_id) + (b.trader_id === traderId ? " (you)" : "") + "</th><td>" + usd.format(b.total_value) +
        "</td><td class='" + cls(b.pnl_pct) + "'><b>" + pct(b.pnl_pct) + "</b></td></tr>";
    }), "No traders yet.", 4);

    fill($("sym"), Object.keys(px.prices || {}).sort());
    fill($("ssym"), pf.positions.map(function (p) { return p.symbol; }));
  }

  function fill(sel, items) {
    var cur = sel.value;
    sel.innerHTML = items.length ? items.map(function (s) { return "<option>" + esc(s) + "</option>"; }).join("") : "<option value=''>(none)</option>";
    if (cur && items.indexOf(cur) >= 0) sel.value = cur;
  }

  function trade(btn, path, body, okText) {
    var m = $("msg"); btn.disabled = true; m.className = "msg"; m.textContent = "Placing order…";
    api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) { m.className = "msg gain"; m.textContent = okText(r); refresh(); })
      .catch(function (e) { m.className = "msg loss"; m.textContent = "Order not placed: " + e.message; })
      .then(function () { btn.disabled = false; });
  }
  $("buy").addEventListener("click", function () {
    var amt = parseFloat($("amt").value);
    if (!$("sym").value || !(amt > 0)) { $("msg").className = "msg loss"; $("msg").textContent = "Choose a name and an amount above 0."; return; }
    trade(this, "/traders/buy", { trader_id: traderId, symbol: $("sym").value, amount_usd: amt },
      function (r) { return "Bought " + num(r.qty) + " " + r.symbol + " at " + usd.format(r.price) + "."; });
  });
  $("sell").addEventListener("click", function () {
    if (!$("ssym").value) { $("msg").className = "msg loss"; $("msg").textContent = "You have nothing to sell."; return; }
    trade(this, "/traders/sell", { trader_id: traderId, symbol: $("ssym").value, percent: parseFloat($("pct").value) },
      function (r) { return "Sold " + num(r.qty) + " " + r.symbol + " at " + usd.format(r.price) + ". Result: " + money(r.realized_pnl) + "."; });
  });

  function load() {
    var v = $("trader").value.trim();
    if (!v) return;
    traderId = v;
    try { localStorage.setItem("tb-trader", v); } catch (e) {}
    refresh();
  }
  $("load").addEventListener("click", load);
  $("trader").addEventListener("keydown", function (e) { if (e.key === "Enter") load(); });

  // Accessibility prefs
  var root = document.documentElement;
  function setPref(kind, val) {
    if (kind === "size") root.setAttribute("data-size", val);
    else if (val === "auto") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", val);
    document.querySelectorAll("[data-" + kind + "]").forEach(function (b) { b.setAttribute("aria-pressed", b.getAttribute("data-" + kind) === val); });
    try { localStorage.setItem("tb-" + kind, val); } catch (e) {}
  }
  document.querySelectorAll("[data-size]").forEach(function (b) { b.addEventListener("click", function () { setPref("size", b.getAttribute("data-size")); }); });
  document.querySelectorAll("[data-theme]").forEach(function (b) { b.addEventListener("click", function () { setPref("theme", b.getAttribute("data-theme")); }); });
  ["size", "theme"].forEach(function (k) { try { var v = localStorage.getItem("tb-" + k); if (v) setPref(k, v); } catch (e) {} });
  try { var t = localStorage.getItem("tb-trader"); if (t) { traderId = t; $("trader").value = t; } } catch (e) {}

  refresh();
  timer = setInterval(refresh, 15000);
})();
