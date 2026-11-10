/* Snake, ported from Doll's GameMaker arcade: how it looks and how it's controlled. The rules are rules.js; the
   menus, coins and showdowns are the shared arcade shell (games/arcade/arcade.js).

   The body is drawn the way the GameMaker game's showdown view draws it: round segments joined by a line, bright
   green at the head fading darker toward the tail. Food glows: red is 1 point, yellow 5, aqua 10.

   Controls: the arrows (or a remote's) turn; up to two turns wait their turn, so quick corners work. In a solo game
   the first arrow starts it. On a touch screen, swipe the way to go. */
(function () {
  "use strict";

  const S = window.SnakeRules, G = window.MGame;
  const CELL = 32, BW = S.W * CELL, BH = S.H * CELL;        // 640 x 480, like Breakout's arena
  const FOOD = { 2: "#ff3b3b", 3: "#ffe14d", 4: "#00f0ff" };
  const DIRS = { right: 0, down: 1, left: 2, up: 3 };

  function frame(ctx, x, y) {                               // the board and its neon frame
    ctx.fillStyle = "rgb(10,12,18)"; ctx.fillRect(x, y, BW, BH);
    ctx.fillStyle = "rgb(20,23,32)";
    for (let cx = 0; cx < S.W; cx++) for (let cy = 0; cy < S.H; cy++) ctx.fillRect(x + cx * CELL + 1, y + cy * CELL + 1, CELL - 2, CELL - 2);
    ctx.save(); ctx.shadowColor = "#00f0ff"; ctx.shadowBlur = 16; ctx.strokeStyle = "rgb(110,188,236)"; ctx.lineWidth = 2;
    ctx.strokeRect(x - 4, y - 4, BW + 8, BH + 8); ctx.restore();
  }

  function food(ctx, x, y, f, t) {                          // a glowing dot that breathes
    if (!f || f.length < 3) return;
    const pulse = G.reducedMotion ? 0.5 : (Math.sin(t * 0.006) + 1) / 2, colour = FOOD[f[2]] || FOOD[2];
    ctx.save(); ctx.shadowColor = colour; ctx.shadowBlur = 10 + pulse * 12; ctx.fillStyle = colour;
    ctx.beginPath(); ctx.arc(x + (f[0] + 0.5) * CELL, y + (f[1] + 0.5) * CELL, 8 + pulse * 3, 0, Math.PI * 2); ctx.fill(); ctx.restore();
  }

  function body(ctx, x, y, points, alive) {                 // segment centres (canvas units), head first
    const n = points.length;
    if (!n) return;
    ctx.lineCap = "round"; ctx.lineJoin = "round";
    for (let i = n - 1; i >= 0; i--) {
      const t = i / Math.max(1, n - 1), p = points[i];
      if (i < n - 1) {                                       // the line toward the tail
        ctx.strokeStyle = alive ? "rgb(" + (44 + Math.floor(70 * (1 - t))) + "," + (126 + Math.floor(54 * (1 - t))) + ",72)" : "rgb(90,90,100)";
        ctx.lineWidth = 14; ctx.beginPath(); ctx.moveTo(x + p[0], y + p[1]); ctx.lineTo(x + points[i + 1][0], y + points[i + 1][1]); ctx.stroke();
      }
      ctx.fillStyle = alive ? "rgb(" + (58 + Math.floor(80 * (1 - t))) + "," + (180 + Math.floor(40 * (1 - t))) + ",86)" : "rgb(120,120,130)";
      ctx.beginPath(); ctx.arc(x + p[0], y + p[1], i === 0 ? 13 : 11, 0, Math.PI * 2); ctx.fill();
    }
    const head = points[0];                                   // two little eyes
    ctx.fillStyle = "#07020d";
    ctx.beginPath(); ctx.arc(x + head[0] - 4, y + head[1] - 3, 2.5, 0, Math.PI * 2); ctx.arc(x + head[0] + 4, y + head[1] - 3, 2.5, 0, Math.PI * 2); ctx.fill();
  }

  const game = {
    slug: "snake", name: "Snake",
    load: function (add) { add("eat", "assets/eat.wav", 2); },
    create: function (seed, race) {
      return { state: S.newGame(seed), queue: [], started: race, frames: 0, swipe: null };
    },
    step: function (g, keys, events) {
      if (!g.started || !g.state.alive) return;
      if (++g.frames % S.FRAMES_PER_MOVE) return;            // one move every eight frames
      let dir = g.state.heading;
      while (g.queue.length) {                               // the next queued turn that isn't straight back
        const next = g.queue.shift();
        if (next !== dir && next !== (dir + 2) % 4) { dir = next; break; }
      }
      S.step(g.state, dir, events);
    },
    effects: function (g, events, play) {
      events.forEach(function (e) {
        if (e.type === "eat") play("eat", 0.3);
        else if (e.type === "die") G.sound.tone(260, 0.6, 0, 0.3, "sawtooth", 50);
      });
    },
    action: function (g, action) {
      if (action === "enter" && !g.started) { g.started = true; return; }
      if (!(action in DIRS)) return;
      g.started = true;
      if (g.queue.length < 2) g.queue.push(DIRS[action]);
    },
    pointer: function (g, x, y, down) { if (down) g.swipe = [x, y]; },
    pointerUp: function (g, at) {                            // a swipe turns that way
      if (!g.swipe) return;
      const dx = at.x - g.swipe[0], dy = at.y - g.swipe[1];
      g.swipe = null;
      if (Math.max(Math.abs(dx), Math.abs(dy)) < 12) { game.action(g, "enter"); return; }
      game.action(g, Math.abs(dx) > Math.abs(dy) ? (dx > 0 ? "right" : "left") : (dy > 0 ? "down" : "up"));
    },
    over: function (g) { return !g.state.alive; },
    claim: function (g) { return { moves: g.state.moves, turns: g.state.turns, died: !g.state.alive }; },
    board: function (g) { return S.board(g.state); },
    hud: function (g) { return "Score " + g.state.score + " · Length " + g.state.body.length; },
    boardHud: function (b) { return "Score " + b.score + " · Length " + b.length + (b.alive ? "" : " · out"); },
    waiting: function (g) { return g.started ? "" : "Press an arrow to start"; },

    drawArena: function (ctx, g, x, y, t) {
      frame(ctx, x, y);
      food(ctx, x, y, g.state.food, t);
      body(ctx, x, y, g.state.body.map(function (c) { return [(c[0] + 0.5) * CELL, (c[1] + 0.5) * CELL]; }), g.state.alive);
    },

    drawBoard: function (ctx, b, x, y, t) {                  // another racer's game, from their live board
      frame(ctx, x, y);
      if (!b) {
        ctx.fillStyle = "#b98aa9"; ctx.font = "600 22px " + G.colors().font; ctx.textAlign = "center";
        ctx.fillText("Waiting for their game…", x + BW / 2, y + BH / 2);
        return;
      }
      food(ctx, x, y, b.food, t);
      const points = [];
      for (let i = 0; i + 1 < b.points.length; i += 2) points.push([b.points[i] * BW, b.points[i + 1] * BH]);
      body(ctx, x, y, points, b.alive);
    },

    drawIdle: function (ctx, t, W, H) {                      // behind the menus: the name in neon and a snake winding by
      ctx.save(); ctx.shadowColor = "#7dff3a"; ctx.shadowBlur = 30;
      ctx.font = "800 120px " + G.colors().font; ctx.textAlign = "center"; ctx.fillStyle = "#ffe9f5";
      ctx.fillText("SNAKE", W / 2, 150); ctx.restore();
      const points = [];
      for (let i = 0; i < 14; i++) points.push([W / 2 - 220 + i * 32, 200 + Math.sin(t / 400 - i * 0.5) * 18]);
      body(ctx, 0, 0, points.reverse(), true);
    },
  };

  window.Arcade.start(game);
})();
