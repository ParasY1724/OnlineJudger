import json
import boto3
import os
import subprocess
import uuid
import shutil

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

def run_untrusted_code(cmd_list, work_dir, timeout=2):
    try:
        process = subprocess.run(
            cmd_list,
            cwd=work_dir,
            env=get_safe_env(), # STRIP AWS CREDENTIALS!
            capture_output=True,
            timeout=timeout
        )
        if process.returncode == 0:
            return "AC", process.stdout.decode('utf-8')
        else:
            return "RE", process.stderr.decode('utf-8')
    except subprocess.TimeoutExpired:
        return "TLE", "Time Limit Exceeded"

def execute_cpp(code, work_dir):
    source_file = os.path.join(work_dir, "solution.cpp")
    executable = os.path.join(work_dir, "a.out")
    
    with open(source_file, "w") as f:
        f.write(code)
        
    try:
        subprocess.run(["g++", "-O2", source_file, "-o", executable], cwd=work_dir, check=True, capture_output=True, timeout=5)
    except subprocess.CalledProcessError as e:
        return "CE", e.stderr.decode('utf-8')
        
    return run_untrusted_code([executable], work_dir)

def execute_python(code, work_dir):
    source_file = os.path.join(work_dir, "solution.py")
    
    with open(source_file, "w") as f:
        f.write(code)
        
    return run_untrusted_code(["python3", source_file], work_dir)

def execute_java(code, work_dir):
    source_file = os.path.join(work_dir, "Solution.java")
    
    with open(source_file, "w") as f:
        f.write(code)
        
    try:
        subprocess.run(["javac", source_file], cwd=work_dir, check=True, capture_output=True, timeout=5)
    except subprocess.CalledProcessError as e:
        return "CE", e.stderr.decode('utf-8')
        
    # Restrict Java heap to 128MB to fit within Lambda's 256MB limit safely
    return run_untrusted_code(["java", "-Xmx128m", "Solution"], work_dir)

def execute_javascript(code, work_dir):
    source_file = os.path.join(work_dir, "solution.js")
    
    with open(source_file, "w") as f:
        f.write(code)
        
    # Restrict Node heap
    return run_untrusted_code(["node", "--max-old-space-size=128", source_file], work_dir)

def execute_go(code, work_dir):
    source_file = os.path.join(work_dir, "main.go")
    executable = os.path.join(work_dir, "main")
    
    with open(source_file, "w") as f:
        f.write(code)
        
    try:
        # Go requires temporary cache directories. Redirect them to /tmp
        go_env = get_safe_env()
        go_env["GOCACHE"] = os.path.join(work_dir, ".cache")
        go_env["GOMODCACHE"] = os.path.join(work_dir, ".modcache")
        
        subprocess.run(["go", "build", "-o", executable, source_file], cwd=work_dir, env=go_env, check=True, capture_output=True, timeout=5)
    except subprocess.CalledProcessError as e:
        return "CE", e.stderr.decode('utf-8')
        
    return run_untrusted_code([executable], work_dir)


def handler(event, context):
    """Main entrypoint triggered by SQS."""
    for record in event['Records']:
        payload = json.loads(record['body'])
        sub_id = payload.get('submissionId')
        code = payload.get('sourceCode')
        lang = payload.get('language')
        
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
            if lang == 'cpp': verdict, output = execute_cpp(code, work_dir)
            elif lang == 'python': verdict, output = execute_python(code, work_dir)
            elif lang == 'java': verdict, output = execute_java(code, work_dir)
            elif lang == 'javascript': verdict, output = execute_javascript(code, work_dir)
            elif lang == 'go': verdict, output = execute_go(code, work_dir)
            else: verdict, output = "RE", f"Unsupported language: {lang}"
        except Exception as e:
            verdict, output = "RE", str(e)
        finally:
            # Prevent workspace persistence across Lambda warm starts
            shutil.rmtree(work_dir, ignore_errors=True)
            
        result_payload = {
            "submissionId": sub_id, 
            "verdict": verdict,
            "output": output[:1000] # Truncate large outputs (for low memory ussage in sqs)
        }
        
        sqs.send_message(QueueUrl=RESULT_QUEUE_URL, MessageBody=json.dumps(result_payload))
        
        table.update_item(
            Key={'submissionId': sub_id},
            UpdateExpression='SET #st = :v1, output = :v2',
            ExpressionAttributeNames={'#st': 'status'},
            ExpressionAttributeValues={':v1': verdict, ':v2': result_payload["output"]}
        )
        
    return {"statusCode": 200, "body": "Processed"}