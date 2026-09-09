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
      asset.name === `AutoSubtitlePlus-${edition}-Windows-x64.zip` &&
      asset.browser_download_url?.startsWith(`https://github.com/${repository}/releases/download/`));
    const release = releases
      .filter(item => !item.draft && assetFor(item, "GUI") && assetFor(item, "CLI"))
      .sort((a, b) => Date.parse(b.published_at) - Date.parse(a.published_at))[0];
    if (!release) return;
    document.querySelectorAll("[data-download]").forEach(link => {
      link.href = assetFor(release, link.dataset.download).browser_download_url;
    });
    document.querySelectorAll("[data-release-label]").forEach(label => {
      label.textContent = `${release.tag_name} · ${release.prerelease ? "Pre-release" : "Stable release"}`;
    });
    const notesUrl = `https://github.com/${repository}/releases/tag/${encodeURIComponent(release.tag_name)}`;
    document.querySelectorAll("[data-release-notes]").forEach(link => { link.href = notesUrl; });
  } catch {
    // The static download links remain available without a successful API request.
  }
})();
