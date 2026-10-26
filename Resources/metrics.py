#!/usr/bin/python3
"""Read interface counters and take tiny reachability samples without emitting endpoints."""
import concurrent.futures
import re
import subprocess
import time

def run(args):
    return subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)


def physical_interface():
    result = run(['/sbin/route', '-n', 'get', 'default'])
    match = re.search(r'^\s*interface:\s*(en\d+)\s*$', result.stdout, re.M)
    return match.group(1) if match else None


def counters(name):
    if not name:
        return 0, 0
    result = run(['/usr/sbin/netstat', '-I', name, '-b', '-n'])
    lines = result.stdout.splitlines()
    if len(lines) < 2:
        return 0, 0
    if 'Ibytes' not in lines[0] or 'Obytes' not in lines[0]:
        return 0, 0
    for line in lines[1:]:
        fields = line.split()
        if fields and fields[0] == name and '<Link#' in line:
            try:
                return int(fields[-5]), int(fields[-2])
            except (IndexError, ValueError):
                return 0, 0
    return 0, 0


def physical_interfaces():
    result = run(['/sbin/ifconfig', '-a'])
    names = []
    for match in re.finditer(r'(?ms)^(en\d+):.*?(?=^\S|\Z)', result.stdout):
        block = match.group(0)
        if 'status: active' in block:
            names.append(match.group(1))
    if not names:
        fallback = physical_interface()
        if fallback:
            names.append(fallback)
    return names


def total_counters(names):
    values = [counters(name) for name in names]
    return sum(value[0] for value in values), sum(value[1] for value in values)


def ping(target, active=True):
    if not active:
        return None, False
    tool = '/sbin/ping6' if ':' in target else '/sbin/ping'
    args = [tool, '-n', '-q', '-c', '1'] + ([] if ':' in target else ['-W', '1200']) + [target]
    try:
        result = run(args)
    except subprocess.TimeoutExpired:
        return None, False
    match = re.search(r'=\s*[\d.]+/([\d.]+)/', result.stdout)
    return (round(float(match.group(1)), 1), True) if result.returncode == 0 and match else (None, False)


def snapshot(nodes=(), probe=True):
    definitions = [(n['id'], n['name'], n['flag'], (n.get('resolved') or [n['address']])[0]) for n in nodes]
    if probe and definitions:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(16, len(definitions))) as pool:
            probes = list(pool.map(lambda item: ping(item[3]), definitions))
    else:
        probes = [(None, False)] * len(definitions)
    paths = [{'id': item[0], 'name': item[1], 'flag': item[2], 'success': success, 'latency': latency}
             for item, (latency, success) in zip(definitions, probes)]
    received, sent = total_counters(physical_interfaces())
    return {'checked': time.time(), 'probed': probe, 'paths': paths, 'rx': received, 'tx': sent}
