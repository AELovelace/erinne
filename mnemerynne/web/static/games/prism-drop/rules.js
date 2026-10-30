/* Prism Drop's rules (docs: help/user/210-media.md, Games). Pure: no page, no canvas, so `node --test`
   (tests/js/prism_rules.test.mjs) checks them. Ported from MommyBot's ball drop (src/balldrop/rules.js) without
   the wager: the field, entry pins, blocks, bombs and coin pegs are the same, and payouts became points.

   draw(n) returns a whole number from 0 to n-1; the game passes a crypto-backed one, the tests a scripted one.
   Loads as a plain script (window.PrismRules) or as a CommonJS module (the tests). */
(function (root, make) {
  const rules = make();
  if (typeof module === "object" && module.exports) module.exports = rules; else root.PrismRules = rules;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const WIDTH = 10, HEIGHT = 20, DROPS = 10;                     // ten pins across, twenty rows, ten drops a game
  const OBSTACLE_COUNTS = { block: 11, bomb: 9, coin: 8 };       // special pegs shuffled onto each drop's field
  const COIN_POINTS = { min: 1, max: 5 };                        // a coin peg's bonus, once per peg per drop
  const POINTS = [20, 15, 10];                                   // landing that many pockets from the pick: 0, 1, 2
  const BLAST_DIRECTIONS = [[-1, -1], [0, -1], [1, -1], [-1, 0], [1, 0], [-1, 1], [0, 1], [1, 1]];   // column, row

  function cryptoDraw(n) { // A fair whole number below n from the browser's crypto (rejection sampling, no modulo bias).
    const limit = Math.floor(0x100000000 / n) * n, word = new Uint32Array(1);
    do { crypto.getRandomValues(word); } while (word[0] >= limit);
    return word[0] % n;
  }

  function randomObstacles(draw) { // 28 special pegs in unique cells across rows 1-19; row 0 (the entry row) stays clear.
    const cells = [];
    for (let i = 0; i < (HEIGHT - 1) * WIDTH; i++) cells.push({ row: 1 + Math.floor(i / WIDTH), column: 1 + i % WIDTH });
    const layout = [];
    Object.keys(OBSTACLE_COUNTS).forEach(function (type) {
      for (let n = 0; n < OBSTACLE_COUNTS[type]; n++) {
        const pick = draw(cells.length);
        layout.push({ row: cells[pick].row, column: cells[pick].column, type: type });
        cells[pick] = cells[cells.length - 1]; cells.pop();   // without replacement: every peg gets its own cell
      }
    });
    return layout;
  }

  function reflect(value, low, high) { // Bounce a position back inside low..high, like a ball off the walls.
    while (value < low || value > high) value = value < low ? low * 2 - value : high * 2 - value;
    return value;
  }

  function drop(draw, obstacles) { // One drop: the path through the field, which pegs it hit, and the coin bonus.
    const cells = new Map(), spent = new Set();
    obstacles.forEach(function (o) { cells.set(o.row + ":" + o.column, o.type); });
    const trajectory = [{ row: 0, column: 4 + draw(4) }];        // enters at pin 4, 5, 6 or 7
    let bonus = 0;
    while (trajectory[trajectory.length - 1].row < HEIGHT) {
      const point = trajectory[trajectory.length - 1], key = point.row + ":" + point.column, type = cells.get(key);
      let column, row;
      if (type === "bomb" && !spent.has(key)) {
        spent.add(key); point.hit = "bomb";                       // each bomb fires once, so the ball always comes down
        const dir = BLAST_DIRECTIONS[draw(BLAST_DIRECTIONS.length)], distance = 2 + draw(2);
        column = reflect(point.column + dir[0] * distance, 1, WIDTH);
        row = Math.min(HEIGHT, Math.abs(point.row + dir[1] * distance));   // off the ceiling; a downward blast may finish
      } else {
        if (type === "block") point.hit = "block";
        if (type === "coin" && !spent.has(key)) {
          spent.add(key); point.hit = "coin";
          point.coins = COIN_POINTS.min + draw(COIN_POINTS.max - COIN_POINTS.min + 1); bonus += point.coins;
        }
        column = reflect(point.column + (draw(2) === 0 ? -1 : 1) * (type === "block" ? 2 : 1), 1, WIDTH);  // a block shoves two
        row = point.row + 1;
      }
      trajectory.push({ row: row, column: column });
    }
    return { obstacles: obstacles, trajectory: trajectory, landing: trajectory[trajectory.length - 1].column, bonus: bonus };
  }

  function landingPoints(pick, landing) { // 20 for the right pocket, 15 one away, 10 two away, nothing further.
    const distance = Math.abs(pick - landing);
    return distance < POINTS.length ? POINTS[distance] : 0;
  }

  function play(pick, draw) { // A whole drop for this pick: a fresh field, the path, and what it scored.
    draw = draw || cryptoDraw;
    const round = drop(draw, randomObstacles(draw));
    round.pick = pick;
    round.landingPoints = landingPoints(pick, round.landing);
    round.points = round.landingPoints + round.bonus;
    return round;
  }

  return { WIDTH: WIDTH, HEIGHT: HEIGHT, DROPS: DROPS, OBSTACLE_COUNTS: OBSTACLE_COUNTS, COIN_POINTS: COIN_POINTS, POINTS: POINTS,
           cryptoDraw: cryptoDraw, randomObstacles: randomObstacles, drop: drop, landingPoints: landingPoints, play: play };
});
