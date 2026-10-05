const vscode = require('vscode');
const fs = require('node:fs');
const path = require('node:path');
const { LanguageClient } = require('vscode-languageclient/node');
let client;

async function activate(context) {
  if (!vscode.workspace.isTrusted) return;
  const config = vscode.workspace.getConfiguration('noscheme');
  let command = config.get('serverPath');
  if (!command) {
    for (const folder of vscode.workspace.workspaceFolders || []) {
      const candidate = path.join(folder.uri.fsPath, '.venv',
        process.platform === 'win32' ? 'Scripts/noscheme-lsp.exe' : 'bin/noscheme-lsp');
      if (fs.existsSync(candidate)) { command = candidate; break; }
    }
  }
  client = new LanguageClient('noscheme', 'NoScheme', {
    command: command || 'noscheme-lsp', args: config.get('serverArgs', []),
  }, {
    documentSelector: [{ scheme: 'file', language: 'noscheme' }],
  });
  context.subscriptions.push(client);
  await client.start();
}
async function deactivate() {
  if (client) await client.stop();
}
module.exports = { activate, deactivate };
