/**
 * pi 适配器：把 cs-study 学习环境的工具注册给 pi。这里没有业务逻辑，只做转发。
 *
 * 工具的定义和实现都在 Python（studykit/app/harness.py，入口 studykit/agent_tools.py）。这个文件启动时问 Python
 * 要工具清单，模型每次调用工具时再把参数交给 Python 执行。
 * 由 harness（studykit/adapters/pi.py）通过环境变量告诉它：用哪个 Python、仓库在哪、本次运行目录、开放哪些工具。
 */
import { execFileSync } from "node:child_process";
import { Type } from "@earendil-works/pi-ai";
import { defineTool, type ExtensionAPI } from "@earendil-works/pi-coding-agent";

const PYTHON = process.env.STUDY_PYTHON ?? "python";
const ROOT = process.env.STUDY_ROOT ?? process.cwd();
const RUN_DIR = process.env.STUDY_RUN_DIR ?? "";
const TOOLS = process.env.STUDY_TOOLS ?? "";

function py(args: string[], input?: string): string {
	return execFileSync(PYTHON, ["-m", "studykit.agent_tools", ...args], {
		cwd: ROOT,
		input,
		encoding: "utf-8",
		env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
		maxBuffer: 16 * 1024 * 1024,
		timeout: 120_000,
	});
}

export default function (pi: ExtensionAPI) {
	if (!RUN_DIR) return; // 不是由 runner 启动的，不注册任何工具
	const specs = JSON.parse(py(TOOLS ? ["schema", TOOLS] : ["schema"])) as {
		name: string;
		description: string;
		parameters: Record<string, unknown>;
	}[];
	for (const spec of specs) {
		pi.registerTool(
			defineTool({
				name: spec.name,
				label: spec.name,
				description: spec.description,
				parameters: Type.Unsafe<Record<string, unknown>>(spec.parameters),
				async execute(_id, params) {
					const out = JSON.parse(py(["call", spec.name, RUN_DIR], JSON.stringify(params ?? {}))) as {
						ok: boolean;
						text: string;
					};
					if (!out.ok) throw new Error(out.text); // 抛错 = 失败的工具结果，模型会看到错误信息并调整
					return { content: [{ type: "text", text: out.text }], details: undefined };
				},
			}),
		);
	}
}
