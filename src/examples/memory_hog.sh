#!/bin/bash
# Memory hog: exponential string doubling to trigger 128 MB cgroup limit (HTTP 422 'oom_killed')
data="x"
while true; do
    data="$data$data"
done
