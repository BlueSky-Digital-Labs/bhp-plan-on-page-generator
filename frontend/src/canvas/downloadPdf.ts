// A4 landscape at 96 dpi: the width the one-pager is laid out at for the file, whatever the screen.
const EXPORT_WIDTH_PX = 1123;
const PAGE_MM = { width: 297, height: 210 };
const PAGE_BACKGROUND = [30, 30, 30] as const; // the canvas background, so any spare margin matches

// Renders the one-pager on screen into a single A4 landscape PDF and downloads it. Everything happens
// in the browser: the canvas content is never sent anywhere. The libraries load only when this runs.
export async function downloadOnePagerPdf(onePager: HTMLElement, filename: string): Promise<void> {
  const [{ default: html2canvas }, { jsPDF }] = await Promise.all([import("html2canvas"), import("jspdf")]);

  // Draw a copy at the fixed desktop width, off-screen, so the file looks the same on a phone.
  const stage = document.createElement("div");
  stage.className = "one-pager-export";
  stage.setAttribute("aria-hidden", "true");
  stage.appendChild(onePager.cloneNode(true));
  document.body.appendChild(stage);
  try {
    const canvas = await html2canvas(stage, {
      width: EXPORT_WIDTH_PX,
      windowWidth: EXPORT_WIDTH_PX,
      scale: 2,
      backgroundColor: null,
      logging: false,
    });
    const pdf = new jsPDF({ orientation: "landscape", unit: "mm", format: "a4" });
    pdf.setFillColor(...PAGE_BACKGROUND);
    pdf.rect(0, 0, PAGE_MM.width, PAGE_MM.height, "F");
    // Fit the whole canvas on the one page, keeping its proportions.
    const scale = Math.min(PAGE_MM.width / canvas.width, PAGE_MM.height / canvas.height);
    const width = canvas.width * scale;
    const height = canvas.height * scale;
    pdf.addImage(canvas.toDataURL("image/jpeg", 0.95), "JPEG", (PAGE_MM.width - width) / 2, (PAGE_MM.height - height) / 2, width, height);
    pdf.save(filename);
  } finally {
    stage.remove();
  }
}
