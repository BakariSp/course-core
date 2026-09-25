#!/usr/bin/env bash
# 练习用 shell 环境自检 —— 对应 curriculum.md 的 tools-01-shell（MIT Missing Semester：shell 一讲）。
#
# 用法（在哪都行，不依赖仓库路径）：
#   Git Bash:  bash scripts/check-shell-env.sh
#   WSL:       bash /mnt/d/cs-study/scripts/check-shell-env.sh
#
# 只读检查：临时文件都在 mktemp 目录里，跑完会删掉；不改你任何东西。
# 退出码 0 = 核心工具齐了，可以跟着做练习。

set -u

pass=0
fail=0
ok() { printf '  ok   %s\n' "$1"; pass=$((pass + 1)); }
bad() { printf '  FAIL %s\n' "$1"; fail=$((fail + 1)); }
t() { # t <描述> <期望> <实际>
  if [ "$2" = "$3" ]; then ok "$1"; else bad "$1（期望 '$2'，实际 '$3'）"; fi
}

CORE="bash cat ls echo mkdir rm cp mv chmod grep sed awk find xargs sort uniq head tail wc cut tr tee env which less tar curl ssh git python3"
EXTRA="tmux man make gcc rsync tree docker"

echo "==== 练习用 shell 环境自检 ===="
echo

echo "-- 运行环境 --"
printf '  shell  : %s\n' "${BASH_VERSION:-不是 bash}"
printf '  系统   : %s\n' "$(uname -s)"
printf '  HOME   : %s\n' "$HOME"
case "$(uname -s)" in
  MINGW*|MSYS*) kind="Git Bash (MSYS2)";;
  Linux*)       kind="Linux（WSL 或容器）";;
  Darwin*)      kind="macOS";;
  *)            kind="未知";;
esac
printf '  类型   : %s\n' "$kind"
echo

echo "-- 工具齐不齐 --"
missing_core=""
for c in $CORE; do
  command -v "$c" >/dev/null 2>&1 || missing_core="$missing_core $c"
done
if [ -z "$missing_core" ]; then
  ok "核心工具齐全"
else
  bad "缺少核心工具：$missing_core"
fi
missing_extra=""
for c in $EXTRA; do
  command -v "$c" >/dev/null 2>&1 || missing_extra="$missing_extra $c"
done
if [ -z "$missing_extra" ]; then
  ok "加分工具齐全"
else
  printf '  --   加分工具里还缺：%s\n' "$missing_extra"
  printf '       只影响 tmux/man/gcc 那几段；WSL 里用 sudo apt update && sudo apt install -y tmux man-db tree build-essential 补上\n'
fi
echo

echo "-- 功能实测（本讲真正要练的东西）--"
LAB=$(mktemp -d) || { echo "  建不了临时目录，先别继续"; exit 1; }
trap 'cd / 2>/dev/null; rm -rf "$LAB"' EXIT
cd "$LAB" || exit 1

printf 'b\na\n' > in.txt
t "管道 sort | tr" "a,b," "$(printf 'b\na\n' | sort | tr '\n' ',')"
t "输入重定向 <" "a" "$(sort < in.txt | head -1)"
printf 'x\n' > out.txt
printf 'y\n' >> out.txt
t "输出重定向 > 与追加 >>" "2" "$(wc -l < out.txt | tr -d ' ')"
t "命令替换 \$( )" "2" "$(echo "$(wc -l < out.txt | tr -d ' ')")"
t "退出码 \$?" "3" "$( (exit 3); echo $? )"
X=1
export Y=2
t "export 的变量，子 shell 里看得见" "2" "$(bash -c 'echo $Y' | tr -d '\r')"
t "没 export 的变量，子 shell 里看不见" "unset" "$(bash -c 'echo ${X:-unset}' | tr -d '\r')"
mkdir -p globd && touch globd/a.txt globd/b.txt globd/c.log
t "通配符 glob *.txt" "2" "$(printf '%s\n' globd/*.txt | wc -l | tr -d ' ')"
t "find -name" "1" "$(find globd -name '*.log' | wc -l | tr -d ' ')"
t "find | xargs" "c.log" "$(find globd -name '*.log' | xargs -I{} basename {} | tr -d '\r')"
f() { echo "$1-$2"; }
t "自定义函数 + 位置参数" "a-b" "$(f a b)"
sleep 0.2 &
bg=$!
if wait "$bg"; then ok "后台作业 & + wait"; else bad "后台作业 & + wait"; fi
echo 中文测试 > zh.txt
t "UTF-8 中文读写" "中文测试" "$(cat zh.txt)"
printf 'link\n' > target.txt
if ln -s target.txt link.txt 2>/dev/null; then t "符号链接 ln -s" "link" "$(cat link.txt)"; else bad "符号链接 ln -s（MSYS 下可能需要开发者模式）"; fi
if chmod 600 out.txt 2>/dev/null; then ok "chmod（Windows 上是模拟；WSL 里才是真的权限位）"; else bad "chmod"; fi

cd /
rm -rf "$LAB"
echo

echo "-- 结论 --"
printf '  通过 %d 项，失败 %d 项\n' "$pass" "$fail"
if [ "$fail" -eq 0 ]; then
  echo "  这个 shell 可以拿来练 shell 一讲：管道、重定向、变量、find/xargs 都是真的。"
  if [ "$kind" = "Git Bash (MSYS2)" ]; then
    cat <<'EOF'
  提醒：
    - 路径：/d/cs-study 就是 D:\cs-study，但把 /d/... 交给 Windows 程序时它只认 D:\...
    - 换行：shell 脚本必须是 LF。报 "bad interpreter: /bin/bash^M" 就是混进了 CRLF。
    - 到 tmux / apt / 真实权限位那几段，再装 WSL Ubuntu（见 README 的「练习用的 shell」）。
EOF
  fi
else
  echo "  上面 FAIL 的项会在练习时挡住你，先修（缺工具就装，WSL 里用 apt）。"
fi

[ "$fail" -eq 0 ]
