"use strict";

// Keep the verified release links usable when GitHub is unavailable or rate-limited.
(async () => {
  const repository = "Sectumsempra82/auto-subtitle-plus";
  try {
    const response = await fetch(`https://api.github.com/repos/${repository}/releases?per_page=100`, {
      headers: { Accept: "application/vnd.github+json" },
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) return;
    const releases = await response.json();
    if (!Array.isArray(releases)) return;
    const assetFor = (release, edition) => release.assets?.find(asset =>
      asset.name === (edition === "macOS" ? "AutoSubtitlePlus-GUI-macOS-arm64.zip" : `AutoSubtitlePlus-${edition}-Windows-x64.zip`) &&
      asset.browser_download_url?.startsWith(`https://github.com/${repository}/releases/download/`));
    const published = releases.filter(item => !item.draft)
      .sort((a, b) => Date.parse(b.published_at) - Date.parse(a.published_at));
    const windows = published.find(item => assetFor(item, "GUI") && assetFor(item, "CLI"));
    const macOS = published.find(item => assetFor(item, "macOS"));
    const releaseFor = platform => platform === "macOS" ? macOS : windows;
    document.querySelectorAll("[data-download]").forEach(link => {
      const release = releaseFor(link.dataset.download);
      if (release) link.href = assetFor(release, link.dataset.download).browser_download_url;
    });
    document.querySelectorAll("[data-release-label]").forEach(label => {
      const release = releaseFor(label.dataset.releaseLabel);
      if (release) label.textContent = `${release.tag_name} · ${release.prerelease ? "Pre-release" : "Stable release"}`;
    });
    document.querySelectorAll("[data-release-notes]").forEach(link => {
      const release = releaseFor(link.dataset.releaseNotes);
      if (release) link.href = `https://github.com/${repository}/releases/tag/${encodeURIComponent(release.tag_name)}`;
    });
  } catch {
    // The static download links remain available without a successful API request.
  }
})();
