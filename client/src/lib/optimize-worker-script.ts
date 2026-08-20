const workerCode = `
self.onmessage = function(e) {
  var data = e.data;
  var bitmap = data.bitmap;
  var fitW = data.fitW;
  var fitH = data.fitH;

  try {
    var canvas = new OffscreenCanvas(fitW, fitH);
    var ctx = canvas.getContext('2d', { alpha: false });
    if (!ctx) {
      self.postMessage({ error: 'OffscreenCanvas context unavailable' });
      return;
    }

    ctx.fillStyle = '#FFFFFF';
    ctx.fillRect(0, 0, fitW, fitH);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(bitmap, 0, 0, fitW, fitH);
    bitmap.close();

    canvas.convertToBlob({ type: 'image/jpeg', quality: 0.95 }).then(function(blob) {
      self.postMessage({ blob: blob });
    }).catch(function(err) {
      self.postMessage({ error: err.message || 'convertToBlob failed' });
    });
  } catch (err) {
    self.postMessage({ error: (err && err.message) || 'Worker canvas error' });
  }
};
`;

let cachedUrl: string | null = null;

export function getWorkerBlobUrl(): string {
  if (!cachedUrl) {
    const blob = new Blob([workerCode], { type: "application/javascript" });
    cachedUrl = URL.createObjectURL(blob);
  }
  return cachedUrl;
}
