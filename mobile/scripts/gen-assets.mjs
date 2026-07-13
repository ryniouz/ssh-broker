// Generate the 1024 launcher icon + 2732 splash from the uploaded app icon,
// so @capacitor/assets can produce every density. Run from the build dir.
import sharp from "sharp";

const BG = { r: 15, g: 18, b: 22, alpha: 1 }; // #0f1216 splash background

// Full-bleed 1024 icon (the source already has its own green rounded square).
await sharp("resources/icon-src.png")
  .resize(1024, 1024, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
  .png()
  .toFile("resources/icon.png");

// Splash: logo centered on the dark brand background.
const logo = await sharp("resources/icon-src.png")
  .resize(680, 680, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
  .png()
  .toBuffer();

for (const out of ["resources/splash.png", "resources/splash-dark.png"]) {
  await sharp({ create: { width: 2732, height: 2732, channels: 4, background: BG } })
    .composite([{ input: logo, gravity: "center" }])
    .png()
    .toFile(out);
}

console.log("assets generated: icon.png (1024), splash.png/splash-dark.png (2732)");
