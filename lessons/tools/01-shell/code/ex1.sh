#!/bin/bash
# 测试会在一个临时目录里运行：bash ex1.sh
# 那个目录里有 logs/（含 app.log、old app.log、archive/）和空的 work/。
#
# 1. 把 logs/ 这一层所有 .log 里含 ERROR 的行，原样写进 work/errors.txt
# 2. 标准输出上只打印一个数字：错误行数
# 3. 运行两次也不能重复
#
# 在下面写你的命令：

grep -h ERROR logs/*.log > work/errors.txt
grep  -c -h ERROR work/errors.txt