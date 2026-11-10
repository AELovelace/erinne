/* Breakout, ported from Doll's GameMaker arcade: how it looks and how it's controlled. The rules are rules.js; the
   menus, coins and showdowns are the shared arcade shell (games/arcade/arcade.js).

   The look follows the GameMaker game's graphical overhaul: a night gradient, the arena in black with the banner
   faintly behind the bricks, bricks and bat breathing a neon glow, the bat leaving a ghost trail, balls trailing
   sparks, bricks bursting into squares of their colour, and CRT scanlines with a sheen sweeping down.

   Controls: ◀ ▶ (held on a keyboard) move the bat, or point and drag; Enter, ▲, Space or a tap launches the ball.
   A remote's ◀ ▶ nudge the bat along. */
(function () {
  "use strict";

  const R = window.BreakoutRules, G = window.MGame;
  const BASE = document.currentScript.src;
  const COLOURS = [null, "#ff2a2a", "#ffe14d", "#2a6bff", "#2ad44a", "#ff2ad4", "#ff9a2a"];   // red yellow blue green fuchsia orange
  const GLOWS = ["#ffffff", "#00ffff", "#7dff3a", "#ff2ad4"];                                     // white aqua lime fuchsia
  const BALLS = ["#ff4242", "#00f0ff", "#7dff3a", "#ff2ad4", "#ffe14d", "#ff9a2a"];
  const images = { banner: picture("assets/banner.png"), power: [0, 1, 2, 3, 4].map(function (i) { return picture("assets/power-up-" + i + ".png"); }) };

  function picture(file) { const img = new Image(); img.src = new URL(file, BASE).href; return img; }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
  }

  function mixWhite(hex, amount) {                         // merge_color(colour, white, amount)
    const n = parseInt(hex.slice(1), 16), r = n >> 16, g = (n >> 8) & 255, b = n & 255;
    const m = function (c) { return Math.round(c + (255 - c) * amount); };
    return "rgb(" + m(r) + "," + m(g) + "," + m(b) + ")";
  }

  // ---------------------------------------------------------------- arena
  function arenaBack(ctx, x, y, t) {                       // the black arena, the banner behind, the neon frame
    ctx.fillStyle = "#000"; ctx.fillRect(x, y, R.W, R.H);
    if (images.banner.complete && images.banner.naturalWidth) {
      ctx.globalAlpha = 0.3; ctx.drawImage(images.banner, x + 28 + (R.W - 56 - (R.H - 56)) / 2, y + 28, R.H - 56, R.H - 56);
      ctx.globalAlpha = 1;
    }
    ctx.save(); ctx.globalCompositeOperation = "lighter";
    for (let i = 0; i < 8; i++) {                          // draw_neon_roundrect: soft outlines, then a solid one
      ctx.strokeStyle = "rgba(44,94,142,0.05)"; ctx.lineWidth = 2;
      roundRect(ctx, x - 6 - (18 - i * 2), y - 6 - (18 - i * 2), R.W + 12 + (18 - i * 2) * 2, R.H + 12 + (18 - i * 2) * 2, 10); ctx.stroke();
    }
    ctx.restore();
    ctx.strokeStyle = "rgb(44,94,142)"; ctx.lineWidth = 2; roundRect(ctx, x - 6, y - 6, R.W + 12, R.H + 12, 8); ctx.stroke();
  }

  function scanlines(ctx, x, y, t) {                       // the CRT overlay: lines, a slow sheen, darker lines between
    if (G.reducedMotion) return;
    ctx.save(); ctx.beginPath(); ctx.rect(x, y, R.W, R.H); ctx.clip();
    ctx.globalCompositeOperation = "lighter"; ctx.fillStyle = "rgba(85,190,255,0.06)";
    const shift = Math.floor(t / 80) % 4;
    for (let ly = y + shift; ly < y + R.H; ly += 4) ctx.fillRect(x, ly, R.W, 2);
    const band = Math.max(18, R.H * 0.12), at = y + (t / 14) % (R.H + band) - band;
    const sheen = ctx.createLinearGradient(0, at, 0, at + band);
    sheen.addColorStop(0, "rgba(85,190,255,0)"); sheen.addColorStop(0.5, "rgba(85,190,255,0.11)"); sheen.addColorStop(1, "rgba(85,190,255,0)");
    ctx.fillStyle = sheen; ctx.fillRect(x, at, R.W, band);
    ctx.globalCompositeOperation = "source-over"; ctx.fillStyle = "rgba(0,0,0,0.12)";
    for (let ly = y + shift + 2; ly < y + R.H; ly += 4) ctx.fillRect(x, ly, R.W, 2);
    ctx.restore();
  }

  function bricks(ctx, cells, x, y, t) {                   // breathing bricks with a flickering neon halo
    const pulse = (Math.sin(t * 0.0024) + 1) / 2, pad = 2 + pulse * 4;
    cells.forEach(function (colour, i) {
      if (!colour) return;
      const r = R.brickRect(i), bx = x + r.x, by = y + r.y;
      ctx.save(); ctx.globalCompositeOperation = "lighter";
      ctx.strokeStyle = G.reducedMotion ? GLOWS[1] : GLOWS[(i + Math.floor(t / 50)) % 4];
      ctx.globalAlpha = 0.05 + pulse * 0.25; ctx.lineWidth = 1.5;
      roundRect(ctx, bx - pad, by - pad, r.w + pad * 2, r.h + pad * 2, 4); ctx.stroke();
      roundRect(ctx, bx - pad - 2, by - pad - 2, r.w + pad * 2 + 4, r.h + pad * 2 + 4, 5); ctx.stroke();
      ctx.restore();
      ctx.fillStyle = mixWhite(COLOURS[colour] || COLOURS[6], 0.1 + pulse * 0.3);
      ctx.fillRect(bx + 1, by + 1, r.w - 2, r.h - 2);
    });
  }

  function batShape(ctx, x, y, centre, half, t, trail) {   // the dark red bat, its ghost trail, its breathing
    const pulse = (Math.sin(t * 0.0024) + 1) / 2;
    if (trail) {
      ctx.save(); ctx.globalCompositeOperation = "lighter"; ctx.strokeStyle = GLOWS[Math.floor(t / 33) % 4];
      trail.forEach(function (px, i) {
        const fade = 1 - i / trail.length, pad = fade * 6;
        ctx.globalAlpha = 0.04 + fade * 0.18;
        roundRect(ctx, x + px - half - pad, y + R.BAT_Y - R.BAT_H / 2 - pad, half * 2 + pad * 2, R.BAT_H + pad * 2, 5); ctx.stroke();
      });
      ctx.restore();
    }
    ctx.fillStyle = mixWhite("#8a0b1e", 0.1 + pulse * 0.3);
    roundRect(ctx, x + centre - half, y + R.BAT_Y - R.BAT_H / 2, half * 2, R.BAT_H, 4); ctx.fill();
  }

  function ball(ctx, bx, by, r, colour) {
    ctx.save(); ctx.shadowColor = colour; ctx.shadowBlur = 14; ctx.fillStyle = colour;
    ctx.beginPath(); ctx.arc(bx, by, r, 0, Math.PI * 2); ctx.fill(); ctx.restore();
  }

  // --------------------------------------------------------------- module
  const game = {
    slug: "breakout", name: "Breakout",
    load: function (add) {
      for (let n = 2; n <= 9; n++) add("bounce-" + n, "assets/bounce-" + n + ".wav", 2);
      add("lost", "assets/ball-lost.wav", 1);
    },
    create: function (seed, race) {
      return { state: R.newGame(seed, { autoLaunch: race ? 90 : 0 }), target: null, launch: false,
               particles: [], sparks: [], batTrail: [], ballTrails: new Map() };
    },
    step: function (g, keys, events) {
      if (keys.left || keys.right) g.target = null;          // keys took over from the pointer
      R.step(g.state, { left: keys.left, right: keys.right, target: g.target, launch: g.launch }, events);
      g.launch = false;
      g.batTrail.unshift(g.state.bat); if (g.batTrail.length > 10) g.batTrail.pop();
      g.state.balls.forEach(function (b) {                   // sparks behind every moving ball, more the faster it goes
        if (!b.go || G.reducedMotion) return;
        const n = Math.max(1, Math.min(4, Math.round(Math.hypot(b.vx, b.vy) * 0.4)));
        for (let i = 0; i < n; i++) g.sparks.push({ x: b.x, y: b.y, r: 3 + Math.random() * 7, life: 15 + Math.random() * 10, age: 0 });
        const trail = g.ballTrails.get(b) || [];
        if (g.state.frames % 2 === 0) { trail.unshift([b.x, b.y]); if (trail.length > 10) trail.pop(); }
        g.ballTrails.set(b, trail);
      });
      g.sparks = g.sparks.filter(function (s) { return ++s.age < s.life; });
      g.particles = g.particles.filter(function (p) {
        p.x += p.vx; p.y += p.vy; p.vy += 0.08; p.vx *= 0.97; p.size = Math.max(0, p.size - 0.6);
        return ++p.age < p.life && p.size > 0;
      });
    },
    effects: function (g, events, play) {
      events.forEach(function (e) {
        if (e.type === "bounce") play("bounce-" + (2 + Math.floor(Math.random() * 8)), 0.5);
        else if (e.type === "lost") play("lost", 0.3);
        else if (e.type === "brick" && !G.reducedMotion) {   // eighteen squares in the brick's colour
          for (let i = 0; i < 18; i++) {
            const a = Math.random() * Math.PI * 2, s = 1 + Math.random() * 3;
            g.particles.push({ x: e.x, y: e.y, vx: Math.cos(a) * s, vy: Math.sin(a) * s, size: 10 + Math.random() * 19,
                               colour: COLOURS[e.colour] || COLOURS[6], life: 12 + Math.random() * 12, age: 0 });
          }
        } else if (e.type === "power") G.sound.tone(520, 0.18, 0, 0.2, "triangle", 1040);
        else if (e.type === "level") G.sound.tone(660, 0.3, 0, 0.25, "sine", 1320);
        else if (e.type === "over") G.sound.tone(300, 0.7, 0, 0.3, "sawtooth", 60);
      });
    },
    action: function (g, action) {
      if (action === "enter" || action === "up") g.launch = true;
      else if (action === "left" || action === "right") {   // a remote: nudge the bat a good way along
        const from = typeof g.target === "number" ? g.target : g.state.bat;
        g.target = Math.max(0, Math.min(R.W, from + (action === "left" ? -96 : 96)));
      }
    },
    pointer: function (g, x, y, down) {
      if (x >= -40 && x <= R.W + 40) g.target = x;
      if (down) g.launch = true;
    },
    over: function (g) { return g.state.over; },
    claim: function (g) { return { score: g.state.score, level: g.state.level }; },
    board: function (g) { return R.board(g.state); },
    hud: function (g) { return "Score " + g.state.score + " · Level " + g.state.level + " · Lives " + "♥".repeat(Math.max(0, g.state.lives)); },
    boardHud: function (b) { return "Score " + b.score + " · Level " + b.level + " · " + "♥".repeat(b.lives || 0); },
    waiting: function (g) { return g.state.balls.some(function (b) { return !b.go; }) && !g.state.autoLaunch ? "Enter, ▲ or a tap launches" : ""; },

    drawArena: function (ctx, g, x, y, t) {
      const s = g.state;
      arenaBack(ctx, x, y, t);
      bricks(ctx, s.bricks, x, y, t);
      s.powerUps.forEach(function (p) {
        const img = images.power[p.kind];
        if (img.complete && img.naturalWidth) ctx.drawImage(img, x + p.x, y + p.y, 32, 16);
      });
      batShape(ctx, x, y, s.bat, R.batHalf(s), t, g.batTrail);
      ctx.save(); ctx.globalCompositeOperation = "lighter";
      g.sparks.forEach(function (sp) {                       // yellow sparks fading out
        const life = sp.age / sp.life, alpha = life < 0.5 ? 0.8 - life * 0.8 : 0.4 - (life - 0.5) * 0.8;
        ctx.globalAlpha = Math.max(0, alpha); ctx.fillStyle = "#ffe14d";
        ctx.beginPath(); ctx.arc(x + sp.x, y + sp.y, sp.r / 2, 0, Math.PI * 2); ctx.fill();
      });
      g.particles.forEach(function (p) {
        ctx.globalAlpha = 1 - p.age / p.life; ctx.fillStyle = p.colour;
        ctx.fillRect(x + p.x - p.size / 2, y + p.y - p.size / 2, p.size, p.size);
      });
      ctx.restore();
      s.balls.forEach(function (b) {
        const colour = b.main ? "#ff4242" : "#00f0ff", r = R.radius(b);
        (g.ballTrails.get(b) || []).forEach(function (p, i) {
          ctx.globalAlpha = (1 - i / 10) * 0.45; ctx.fillStyle = colour;
          ctx.beginPath(); ctx.arc(x + p[0], y + p[1], r, 0, Math.PI * 2); ctx.fill();
        });
        ctx.globalAlpha = 1;
        ball(ctx, x + b.x, y + b.y, r, colour);
      });
      scanlines(ctx, x, y, t);
    },

    drawBoard: function (ctx, b, x, y, t) {                  // another racer's game, from their live board
      arenaBack(ctx, x, y, t);
      if (!b) {
        ctx.fillStyle = "#b98aa9"; ctx.font = "600 22px " + G.colors().font; ctx.textAlign = "center";
        ctx.fillText("Waiting for their game…", x + R.W / 2, y + R.H / 2);
        return;
      }
      bricks(ctx, b.bricks.split("").map(Number), x, y, t);
      batShape(ctx, x, y, b.bat * R.W, R.BAT_W * (b.wide ? 1.5 : 1) / 2, t, null);
      b.balls.forEach(function (p, i) { ball(ctx, x + p[0] * R.W, y + p[1] * R.H, R.RADIUS, BALLS[i % BALLS.length]); });
      scanlines(ctx, x, y, t);
    },

    drawIdle: function (ctx, t, W, H) {                      // behind the menus: the name in neon and a row of bricks
      ctx.save(); ctx.shadowColor = "#ff2e97"; ctx.shadowBlur = 30;
      ctx.font = "800 120px " + G.colors().font; ctx.textAlign = "center"; ctx.fillStyle = "#ffe9f5";
      ctx.fillText("BREAKOUT", W / 2, 150); ctx.restore();
      const pulse = (Math.sin(t * 0.0024) + 1) / 2;
      for (let i = 0; i < 18; i++) {
        ctx.fillStyle = mixWhite(COLOURS[1 + (i % 6)], 0.1 + pulse * 0.3);
        ctx.fillRect(W / 2 - 18 * 36 / 2 + i * 36, 180, 32, 16);
      }
    },
  };

  window.Arcade.start(game);
})();
