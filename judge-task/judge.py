import os
import json
import subprocess
import tempfile
import boto3
import logging
import sys
import traceback
from typing import Dict, Any

# Configure logging to both console and file
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/tmp/judge.log', mode='a')
    ]
)
logger = logging.getLogger(__name__)

def run_judge():
    """
    Main judge function with comprehensive error handling and logging.
    """
    submission_id = "UNKNOWN"
    result_payload = {}
    sqs_client = None
    result_queue_url = None
    
    try:
        logger.info("=" * 50)
        logger.info("Judge task started. Reading environment variables...")
        
        # Read and validate environment variables
        try:
            submission_id = os.environ['SUBMISSION_ID']
            source_code = os.environ['SOURCE_CODE']
            language = os.environ['LANGUAGE']
            std_input = os.environ['INPUT']
            expected_output = os.environ['EXPECTED_OUTPUT']
            callback_url = os.environ['CALLBACK_URL']
            result_queue_url = os.environ['RESULT_QUEUE_URL']
            
            logger.info(f"Successfully read ENV VARS for submission: {submission_id}")
            logger.info(f"Language: {language}")
            logger.info(f"Source code length: {len(source_code)} characters")
            logger.info(f"Input length: {len(std_input)} characters")
            logger.info(f"Expected output length: {len(expected_output)} characters")
            logger.info(f"Callback URL: {callback_url}")
            logger.info(f"Result queue URL: {result_queue_url}")
            
        except KeyError as e:
            error_msg = f"Missing required environment variable: {e}"
            logger.error(error_msg)
            logger.error("Available environment variables: %s", list(os.environ.keys()))
            sys.exit(1)
        
        # Initialize AWS SQS client
        try:
            logger.info("Initializing AWS SQS client...")
            sqs_client = boto3.client('sqs')
            logger.info("SQS client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize SQS client: {e}")
            logger.error("Traceback: %s", traceback.format_exc())
            sys.exit(1)
        
        # Initialize result payload
        result_payload = {
            'submissionId': submission_id, 
            'callbackUrl': callback_url
        }
        
        # Validate language support
        if language not in ['py', 'cpp']:
            error_msg = f"Unsupported language: {language}"
            logger.error(error_msg)
            result_payload['status'] = 'RE'
            result_payload['output'] = error_msg
            send_result(sqs_client, result_queue_url, result_payload)
            return
        
        logger.info("Starting code execution process...")
        
        with tempfile.TemporaryDirectory() as temp_dir:
            logger.info(f"Created temporary directory: {temp_dir}")
            
            # Create source file
            file_path = os.path.join(temp_dir, f'source.{language}')
            logger.info(f"Creating source file: {file_path}")
            
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(source_code)
                logger.info("Source file created successfully")
            except Exception as e:
                error_msg = f"Failed to create source file: {e}"
                logger.error(error_msg)
                result_payload['status'] = 'RE'
                result_payload['output'] = error_msg
                send_result(sqs_client, result_queue_url, result_payload)
                return
            
            # Determine compile and run commands
            command = []
            try:
                if language == 'py':
                    logger.info("Setting up Python execution")
                    command = ['python3', file_path]
                    logger.info(f"Python command: {' '.join(command)}")
                    
                elif language == 'cpp':
                    logger.info("Setting up C++ compilation and execution")
                    executable_path = os.path.join(temp_dir, 'a.out')
                    
                    # Compilation Step
                    logger.info("Starting C++ compilation...")
                    compile_command = ['g++', file_path, '-o', executable_path, '-std=c++17', '-O2']
                    logger.info(f"Compile command: {' '.join(compile_command)}")
                    
                    try:
                        compile_proc = subprocess.run(
                            compile_command, 
                            capture_output=True, 
                            text=True, 
                            timeout=10
                        )
                        
                        logger.info(f"Compilation completed with return code: {compile_proc.returncode}")
                        
                        if compile_proc.returncode != 0:
                            error_msg = f"Compilation Error: {compile_proc.stderr.strip()}"
                            logger.error(error_msg)
                            if compile_proc.stdout.strip():
                                logger.error(f"Compilation stdout: {compile_proc.stdout.strip()}")
                            
                            result_payload['status'] = 'CE'
                            result_payload['output'] = compile_proc.stderr.strip()
                            send_result(sqs_client, result_queue_url, result_payload)
                            return
                        
                        logger.info("C++ compilation successful")
                        command = [executable_path]
                        logger.info(f"Execution command: {' '.join(command)}")
                        
                    except subprocess.TimeoutExpired:
                        error_msg = "Compilation timeout (10s exceeded)"
                        logger.error(error_msg)
                        result_payload['status'] = 'CE'
                        result_payload['output'] = error_msg
                        send_result(sqs_client, result_queue_url, result_payload)
                        return
                
            except Exception as e:
                error_msg = f"Error setting up execution: {e}"
                logger.error(error_msg)
                logger.error("Traceback: %s", traceback.format_exc())
                result_payload['status'] = 'RE'
                result_payload['output'] = error_msg
                send_result(sqs_client, result_queue_url, result_payload)
                return
            
            # Execution Step with timeout
            logger.info("Starting code execution...")
            logger.info(f"Execution command: {' '.join(command)}")
            logger.info(f"Input data: {repr(std_input[:100])}{'...' if len(std_input) > 100 else ''}")
            
            try:
                run_proc = subprocess.run(
                    command, 
                    input=std_input, 
                    capture_output=True, 
                    text=True, 
                    timeout=2
                )
                
                logger.info(f"Execution completed with return code: {run_proc.returncode}")
                logger.info(f"Output length: {len(run_proc.stdout)} characters")
                logger.info(f"Error length: {len(run_proc.stderr)} characters")
                
                if run_proc.returncode != 0:
                    error_msg = run_proc.stderr.strip() or "Runtime error (no error message)"
                    logger.error(f"Runtime error: {error_msg}")
                    result_payload['status'] = 'RE'
                    result_payload['output'] = error_msg
                    send_result(sqs_client, result_queue_url, result_payload)
                    return
                
                # Verdict Logic
                actual_output = run_proc.stdout.strip()
                expected_clean = expected_output.strip()
                
                logger.info("Comparing outputs...")
                logger.info(f"Expected output: {repr(expected_clean[:100])}{'...' if len(expected_clean) > 100 else ''}")
                logger.info(f"Actual output: {repr(actual_output[:100])}{'...' if len(actual_output) > 100 else ''}")
                
                if actual_output == expected_clean:
                    logger.info("Output match - ACCEPTED")
                    result_payload['status'] = 'AC'  # Accepted
                else:
                    logger.info("Output mismatch - WRONG ANSWER")
                    result_payload['status'] = 'WA'  # Wrong Answer
                
                result_payload['output'] = actual_output
                
            except subprocess.TimeoutExpired:
                logger.error("Execution timeout (2s exceeded)")
                result_payload['status'] = 'TLE'  # Time Limit Exceeded
                result_payload['output'] = 'Time limit exceeded'
                
            except Exception as e:
                error_msg = f"Execution error: {e}"
                logger.error(error_msg)
                logger.error("Traceback: %s", traceback.format_exc())
                result_payload['status'] = 'RE'
                result_payload['output'] = error_msg
    
    except Exception as e:
        error_msg = f"Unexpected error in judge: {e}"
        logger.error(error_msg)
        logger.error("Traceback: %s", traceback.format_exc())
        
        if not result_payload:
            result_payload = {'submissionId': submission_id}
        result_payload['status'] = 'RE'
        result_payload['output'] = error_msg
    
    finally:
        # Ensure we always send a result
        if sqs_client and result_queue_url and result_payload:
            send_result(sqs_client, result_queue_url, result_payload)
        else:
            logger.error("Cannot send result - missing SQS client, queue URL, or result payload")
            logger.error(f"SQS client: {sqs_client is not None}")
            logger.error(f"Queue URL: {result_queue_url}")
            logger.error(f"Result payload: {bool(result_payload)}")

def send_result(sqs_client, queue_url: str, result_payload: Dict[str, Any]):
    """
    Send result to SQS queue with comprehensive error handling.
    """
    try:
        logger.info("Sending result to SQS queue...")
        logger.info(f"Queue URL: {queue_url}")
        logger.info(f"Result: {json.dumps({k: v for k, v in result_payload.items() if k != 'output'})}")
        logger.info(f"Output length: {len(result_payload.get('output', ''))}")
        
        response = sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps(result_payload)
        )
        
        logger.info(f"Successfully sent message to SQS. MessageId: {response.get('MessageId')}")
        logger.info("Judge task completed successfully")
        
    except Exception as e:
        logger.error(f"Failed to send result to SQS: {e}")
        logger.error("Traceback: %s", traceback.format_exc())
        logger.error(f"Result payload was: {result_payload}")
        # Don't re-raise - we want to exit gracefully
        sys.exit(1)

logger.info("Judge process starting...")
try:
    run_judge()
    logger.info("Judge process completed normally")
except Exception as e:
    logger.error(f"Unhandled exception in main: {e}")
    logger.error("Traceback: %s", traceback.format_exc())
    sys.exit(1)