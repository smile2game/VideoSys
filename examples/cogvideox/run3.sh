#!/bin/bash

# 设置Python脚本路径
PYTHON_SCRIPT="./sample.py"

# 输出日志文件
LOG_FILE="output.log"

# 清空或创建日志文件
> $LOG_FILE

# 定义要运行的模式（根据需要调整）
# MODES=("--base" "--pab" "--low_mem")
MODES=("--base" "--pab")

# 循环执行3次，并记录每次执行的时间
for i in {1..2}; do
    echo "Run #$i:" >> $LOG_FILE
    for mode in "${MODES[@]}"; do
        echo "  Running with mode: $mode" >> $LOG_FILE
        # 使用 time 命令来统计执行时间，并将输出重定向到日志文件
        (time python $PYTHON_SCRIPT $mode) 2>> $LOG_FILE
        echo "" >> $LOG_FILE  # 添加空行以提高可读性
    done
done

echo "All runs completed. Check the results in $LOG_FILE."