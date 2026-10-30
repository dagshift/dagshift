/** Owns the engine process: lazy start, one restart path, user-facing errors. */
import * as vscode from "vscode";
import { EngineClient } from "./engineClient";
import { EngineInstaller } from "./engineInstaller";

export class Engine implements vscode.Disposable {
  private client?: EngineClient;
  private starting?: Promise<EngineClient>;
  private lastErrorShown = 0;
  private readonly installer: EngineInstaller;
  private readonly _onDidStart = new vscode.EventEmitter<EngineClient>();
  readonly onDidStart = this._onDidStart.event;

  constructor(
    ctx: vscode.ExtensionContext,
    private readonly log: vscode.LogOutputChannel,
  ) {
    this.installer = new EngineInstaller(ctx, log);
  }

  get current(): EngineClient | undefined {
    return this.client?.running ? this.client : undefined;
  }

  /** The running engine, starting (and installing) it on first use. */
  get(): Promise<EngineClient> {
    if (this.client?.running) {
      return Promise.resolve(this.client);
    }
    if (!this.starting) {
      this.starting = this.launch().finally(() => {
        this.starting = undefined;
      });
    }
    return this.starting;
  }

  private async launch(force = false): Promise<EngineClient> {
    const cmd = await this.installer.resolve(force);
    await this.client?.dispose();
    const client = new EngineClient({
      command: cmd.command,
      args: cmd.args,
      cwd: vscode.workspace.workspaceFolders?.[0]?.uri.fsPath,
      log: (line) => this.log.debug(line),
    });
    const info = await client.start();
    this.log.info(
      `dagshift ${info.version} engine running (${cmd.source}); targets ${info.targets.map((t) => t.target).join(", ")}`,
    );
    this.client = client;
    this._onDidStart.fire(client);
    return client;
  }

  async restart(reinstall = false): Promise<EngineClient> {
    await this.client?.dispose();
    this.client = undefined;
    this.starting = this.launch(reinstall).finally(() => {
      this.starting = undefined;
    });
    return this.starting;
  }

  /** Show an engine error at most once a minute; everything goes to the log. */
  reportError(err: unknown, context: string): void {
    const msg = err instanceof Error ? err.message : String(err);
    this.log.error(`${context}: ${msg}`);
    if (Date.now() - this.lastErrorShown < 60_000) {
      return;
    }
    this.lastErrorShown = Date.now();
    void vscode.window
      .showErrorMessage(`dagshift: ${msg}`, "Show Log", "Open Settings", "Retry")
      .then((choice) => {
        if (choice === "Show Log") {
          this.log.show();
        } else if (choice === "Open Settings") {
          void vscode.commands.executeCommand("workbench.action.openSettings", "dagshift.interpreter");
        } else if (choice === "Retry") {
          void vscode.commands.executeCommand("dagshift.restartEngine");
        }
      });
  }

  dispose(): void {
    void this.client?.dispose();
    this._onDidStart.dispose();
  }
}
