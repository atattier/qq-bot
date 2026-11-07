function gilbert2d(width, height) {
    const coordinates = [];
    if (width >= height) { generate2d(0, 0, width, 0, 0, height, coordinates); }
    else { generate2d(0, 0, 0, height, width, 0, coordinates); }
    return coordinates;
}
function generate2d(x, y, ax, ay, bx, by, coordinates) {
    const w = Math.abs(ax + ay); const h = Math.abs(bx + by);
    const dax = Math.sign(ax), day = Math.sign(ay);
    const dbx = Math.sign(bx), dby = Math.sign(by);
    if (h === 1) { for (let i = 0; i < w; i++) { coordinates.push([x, y]); x += dax; y += day; } return; }
    if (w === 1) { for (let i = 0; i < h; i++) { coordinates.push([x, y]); x += dbx; y += dby; } return; }
    let ax2 = Math.floor(ax / 2), ay2 = Math.floor(ay / 2);
    let bx2 = Math.floor(bx / 2), by2 = Math.floor(by / 2);
    const w2 = Math.abs(ax2 + ay2); const h2 = Math.abs(bx2 + by2);
    if (2 * w > 3 * h) {
        if ((w2 % 2) && (w > 2)) { ax2 += dax; ay2 += day; }
        generate2d(x, y, ax2, ay2, bx, by, coordinates);
        generate2d(x + ax2, y + ay2, ax - ax2, ay - ay2, bx, by, coordinates);
    } else {
        if ((h2 % 2) && (h > 2)) { bx2 += dbx; by2 += dby; }
        generate2d(x, y, bx2, by2, ax2, ay2, coordinates);
        generate2d(x + bx2, y + by2, ax, ay, bx - bx2, by - by2, coordinates);
        generate2d(x + (ax - dax) + (bx2 - dbx), y + (ay - day) + (by2 - dby),
            -bx2, -by2, -(ax - ax2), -(ay - ay2), coordinates);
    }
}
const out = {};
for (const [w,h] of [[8,8],[16,9],[9,16],[7,5],[1,1],[2,1],[1,7],[13,11],[32,24],[100,1],[1,100]]) {
  out[w+"x"+h] = gilbert2d(w,h);
}
console.log(JSON.stringify(out));
