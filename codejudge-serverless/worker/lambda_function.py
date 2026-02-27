import json
import boto3
import os
import subprocess
import uuid
import shutil
import resource


dynamodb = boto3.resource('dynamodb')
sqs = boto3.client('sqs')

RESULT_QUEUE_URL = os.environ['RESULT_QUEUE_URL']
TABLE_NAME = os.environ['TABLE_NAME']
table = dynamodb.Table(TABLE_NAME)

def get_safe_env():
    return {
        "PATH": "/var/lang/bin:/usr/local/bin:/usr/bin/:/bin:/opt/bin",
        "LANG": "en_US.UTF-8"
    }
    
def set_memory_limit(memory_limit_mb):
    """Returns a preexec_fn that sets the virtual memory limit for the child process."""
    def limit():
        mem_bytes = memory_limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    return limit

def run_untrusted_code(cmd_list, work_dir, timeout=2,memory_limit_mb=256):
    try:
        process = subprocess.run(
            cmd_list,
            cwd=work_dir,
            env=get_safe_env(), # STRIP AWS CREDENTIALS!
            capture_output=True,
            timeout=timeout,
            preexec_fn=set_memory_limit(memory_limit_mb)
        )
        if process.returncode == 0:
            return "AC", process.stdout.decode('utf-8')
        else:
            # returncode -9 (SIGKILL) or -11 (SIGSEGV) often signals MLE
            if process.returncode in (-9, -11):
                return "MLE", "Memory Limit Exceeded"
            return "RE", process.stderr.decode('utf-8')
    except subprocess.TimeoutExpired:
        return "TLE", "Time Limit Exceeded"
    except MemoryError:
        return "MLE", "Memory Limit Exceeded"


def execute_cpp(code, work_dir, timeout, memory_limit_mb):
    source_file = os.path.join(work_dir, "solution.cpp")
    executable = os.path.join(work_dir, "a.out")
    
    with open(source_file, "w") as f:
        f.write(code)
        
    try:
        subprocess.run(["g++", "-O2", source_file, "-o", executable], cwd=work_dir, check=True, capture_output=True, timeout=10)
    except subprocess.CalledProcessError as e:
        return "CE", e.stderr.decode('utf-8')
        
    return run_untrusted_code([executable], work_dir, timeout, memory_limit_mb)

def execute_python(code, work_dir, timeout, memory_limit_mb):
    source_file = os.path.join(work_dir, "solution.py")
    
    with open(source_file, "w") as f:
        f.write(code)
        
    return run_untrusted_code(["python3", source_file], work_dir, timeout, memory_limit_mb)

def execute_java(code, work_dir, timeout, memory_limit_mb):
    source_file = os.path.join(work_dir, "Solution.java")
    
    with open(source_file, "w") as f:
        f.write(code)
        
    try:
        subprocess.run(["javac", source_file], cwd=work_dir, check=True, capture_output=True, timeout=10)
    except subprocess.CalledProcessError as e:
        return "CE", e.stderr.decode('utf-8')
        
    jvm_heap = max(64, memory_limit_mb - 64)
    return run_untrusted_code(
        ["java", f"-Xmx{jvm_heap}m", "Solution"],
        work_dir, timeout, memory_limit_mb
    )

def execute_javascript(code, work_dir, timeout, memory_limit_mb):
    source_file = os.path.join(work_dir, "solution.js")
    
    with open(source_file, "w") as f:
        f.write(code)
        
    node_heap = max(64, memory_limit_mb - 64)
    return run_untrusted_code(
        ["node", f"--max-old-space-size={node_heap}", source_file],
        work_dir, timeout, memory_limit_mb
    )

def execute_go(code, work_dir, timeout, memory_limit_mb):
    source_file = os.path.join(work_dir, "main.go")
    executable = os.path.join(work_dir, "main")
    
    with open(source_file, "w") as f:
        f.write(code)
        
    try:
        # Go requires temporary cache directories. Redirect them to /tmp
        go_env = get_safe_env()
        go_env["GOCACHE"] = os.path.join(work_dir, ".cache")
        go_env["GOMODCACHE"] = os.path.join(work_dir, ".modcache")
        
        subprocess.run(["go", "build", "-o", executable, source_file], cwd=work_dir, env=go_env, check=True, capture_output=True, timeout=10)
    except subprocess.CalledProcessError as e:
        return "CE", e.stderr.decode('utf-8')
        
    return run_untrusted_code([executable], work_dir, timeout, memory_limit_mb)


def compare_output(actual: str, expected: str) -> bool:
    return actual.strip() == expected.strip()


def handler(event, context):
    """Main entrypoint triggered by SQS."""
    for record in event['Records']:
        payload = json.loads(record['body'])
        sub_id = payload.get('submissionId')
        code = payload.get('sourceCode')
        lang = payload.get('language')
        callback_url = payload.get('callback_url')
        expected_output = payload.get('expected_output', '')    
        timeout         = int(payload.get('timeout', 2))  
        memory_limit_mb = int(payload.get('memoryLimit', 256)) 

        
        table.update_item(
            Key={'submissionId': sub_id},
            UpdateExpression='SET #st = :val',
            ExpressionAttributeNames={'#st': 'status'},
            ExpressionAttributeValues={':val': 'PROCESSING'}
        )
        
        # Create unique ephemeral workspace
        run_id = str(uuid.uuid4())
        work_dir = os.path.join("/tmp", run_id)
        os.makedirs(work_dir, exist_ok=True)
        
        try:
            if lang == 'cpp': verdict, output = execute_cpp(code, work_dir,timeout, memory_limit_mb)
            elif lang == 'python': verdict, output = execute_python(code, work_dir,timeout, memory_limit_mb)
            elif lang == 'java': verdict, output = execute_java(code, work_dir,timeout, memory_limit_mb)
            elif lang == 'javascript': verdict, output = execute_javascript(code, work_dir,timeout, memory_limit_mb)
            elif lang == 'go': verdict, output = execute_go(code, work_dir,timeout, memory_limit_mb)
            else: verdict, output = "RE", f"Unsupported language: {lang}"
            
            if verdict == "AC":
                verdict = "AC" if compare_output(output, expected_output) else "WA"
        except Exception as e:
            verdict, output = "RE", str(e)
        finally:
            # Prevent workspace persistence across Lambda warm starts
            shutil.rmtree(work_dir, ignore_errors=True)
            
        result_payload = {
            "submissionId": sub_id, 
            "verdict": verdict,
            "output": output[:1000], # Truncate large outputs (for low memory ussage in sqs)
            "callback_url" : callback_url
        }
        
        sqs.send_message(QueueUrl=RESULT_QUEUE_URL, MessageBody=json.dumps(result_payload))
        
        table.update_item(
            Key={'submissionId': sub_id},
            UpdateExpression='SET #st = :v1, #op = :v2',
            ExpressionAttributeNames={'#st': 'status', '#op': 'result'},
            ExpressionAttributeValues={':v1': verdict, ':v2': result_payload["output"]}
        )
        
    return {"statusCode": 200, "body": "Processed"}