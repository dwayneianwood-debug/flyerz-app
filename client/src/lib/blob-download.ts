export async function blobDownload(
  url: string,
  filename: string,
  onError?: (message: string) => void
): Promise<boolean> {
  try {
    const fullUrl = new URL(url, window.location.origin).href;
    const inIframe = window.self !== window.top;

    if (inIframe) {
      const anchor = document.createElement("a");
      anchor.style.display = "none";
      anchor.href = fullUrl;
      anchor.download = filename;
      anchor.target = "_top";
      document.body.appendChild(anchor);
      anchor.click();
      setTimeout(() => {
        document.body.removeChild(anchor);
      }, 500);
      return true;
    }

    const res = await fetch(url);

    if (!res.ok) {
      let errMsg = `Server returned ${res.status}`;
      try {
        const errData = await res.json();
        errMsg = errData.message || errMsg;
      } catch {}
      throw new Error(errMsg);
    }

    const blob = await res.blob();

    if (blob.size === 0) {
      throw new Error("Downloaded file is empty");
    }

    const objectUrl = window.URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.style.display = "none";
    anchor.href = objectUrl;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();

    setTimeout(() => {
      window.URL.revokeObjectURL(objectUrl);
      document.body.removeChild(anchor);
    }, 200);

    return true;
  } catch (err: any) {
    const message = err.message || "Could not retrieve the file from the server.";
    if (onError) {
      onError(message);
    }
    window.dispatchEvent(
      new CustomEvent("glitchy:download-error", {
        detail: { message: `Error: ${message}` },
      })
    );
    return false;
  }
}
