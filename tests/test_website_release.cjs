const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');
const source = readFileSync('website/release.js', 'utf8');
const prefix = 'https://github.com/Sectumsempra82/auto-subtitle-plus/releases/download/';
const asset = (tag, name) => ({name, browser_download_url: `${prefix}${tag}/${name}`});
const win = {tag_name:'win', published_at:'2026-09-10', assets:['GUI','CLI'].map(x => asset('win', `AutoSubtitlePlus-${x}-Windows-x64.zip`)), prerelease:true};
const mac = {tag_name:'mac', published_at:'2026-09-12', assets:[asset('mac', 'AutoSubtitlePlus-GUI-macOS-arm64.zip')], prerelease:true};
async function run(releases, fails = false) {
  const links = ['GUI','CLI','macOS'].map(download => ({dataset:{download}, href:'fallback'}));
  const labels = ['', 'macOS'].map(releaseLabel => ({dataset:{releaseLabel}, textContent:'fallback'}));
  const notes = ['', 'macOS'].map(releaseNotes => ({dataset:{releaseNotes}, href:'fallback'}));
  const nodes = {'[data-download]':links, '[data-release-label]':labels, '[data-release-notes]':notes};
  await vm.runInNewContext(source, {AbortSignal, document:{querySelectorAll:selector=>nodes[selector]}, fetch:async()=>{ if(fails) throw Error('offline'); return {ok:true,json:async()=>releases}; }});
  return {links,labels,notes};
}
(async()=>{
  const both = await run([win,mac]);
  assert.match(both.links[0].href,/\/win\//);
  assert.match(both.links[1].href,/\/win\//);
  assert.match(both.links[2].href,/\/mac\//);
  assert.equal(both.labels[0].textContent,'win · Pre-release');
  assert.equal(both.labels[1].textContent,'mac · Pre-release');
  assert.match(both.notes[1].href,/\/tag\/mac$/);
  const windowsOnly = await run([win,{...mac,draft:true}]);
  assert.equal(windowsOnly.links[2].href,'fallback');
  const invalid = await run([{...mac, assets:[{...mac.assets[0],browser_download_url:'https://invalid.example/app.zip'}]}]);
  assert.equal(invalid.links[2].href,'fallback');
  const offline = await run([],true);
  assert.ok(offline.links.every(link=>link.href==='fallback'));
  console.log('Release links: platform selection, drafts, URL validation and offline fallback passed.');
})();
