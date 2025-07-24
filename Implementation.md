# Implementation 

## Part 1: Core Infrastructure Setup

### Step 1: Create IAM Role

* Create trust policy for Lambda and ECS.
* Create IAM role and attach the following policies:

  * `AWSLambdaBasicExecutionRole`
  * `AmazonSQSFullAccess`
  * `AmazonDynamoDBFullAccess`
  * `AmazonECS_FullAccess`

📁 [trust-policy.json](./infra/trust-policy.json)

```bash
aws iam create-role --role-name OnlineJudgeServiceRole \
  --assume-role-policy-document file://trust-policy.json

aws iam attach-role-policy --role-name OnlineJudgeServiceRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole

aws iam attach-role-policy --role-name OnlineJudgeServiceRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonSQSFullAccess

aws iam attach-role-policy --role-name OnlineJudgeServiceRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess

aws iam attach-role-policy --role-name OnlineJudgeServiceRole \
  --policy-arn arn:aws:iam::aws:policy/AmazonECS_FullAccess
```

### Step 2: Provision AWS Resources

```bash
aws dynamodb create-table \
  --table-name Submissions \
  --attribute-definitions AttributeName=submissionId,AttributeType=S \
  --key-schema AttributeName=submissionId,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST

aws sqs create-queue --queue-name submission-queue
aws sqs create-queue --queue-name result-queue
```

---

## Part 2: Fargate Judge Task

### Step 3: Prepare Docker Project

📁 [judge.py](./judge-task/judge.py) 

📁 [Dockerfile](./judge-task/Dockerfile) 

📁 [requirements.txt](./judge-task/requirements.txt)

```bash
mkdir judge-task && cd judge-task
pip install boto3
pip freeze > requirements.txt
```

### Step 4: Build & Push Image

```bash
AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
AWS_REGION=$(aws configure get region)
REPO_NAME=online-judge-task

aws ecr create-repository --repository-name ${REPO_NAME}

aws ecr get-login-password --region ${AWS_REGION} | \
  docker login --username AWS --password-stdin ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com

docker build -t ${REPO_NAME} .
docker tag ${REPO_NAME}:latest ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${REPO_NAME}:latest
docker push ${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${REPO_NAME}:latest
```

### Step 5: ECS Cluster & Task Definition

```bash
aws ecs create-cluster --cluster-name online-judge-cluster

aws ecs register-task-definition \
  --family online-judge-task \
  --network-mode awsvpc \
  --requires-compatibilities FARGATE \
  --cpu "1024" \
  --memory "2048" \
  --execution-role-arn ${ROLE_ARN} \
  --task-role-arn ${ROLE_ARN} \
  --container-definitions '[{"name":"judge-container","image":"'${IMAGE_URI}'"}]'
```

---

## Part 3: Lambda Functions

### Step 6: Dispatcher Lambda

📁 [dispatcher/app.py](./dispatcher-lambda/app.py)

```bash
mkdir dispatcher-lambda && cd dispatcher-lambda
zip ../dispatcher.zip app.py && cd ..

aws lambda create-function \
  --function-name dispatcher-lambda \
  --runtime python3.9 \
  --handler app.handler \
  --role ${ROLE_ARN} \
  --zip-file fileb://dispatcher.zip \
  --environment "Variables={ECS_CLUSTER_NAME=online-judge-cluster,ECS_TASK_DEFINITION=online-judge-task,SUBNET_ID=${SUBNET_ID},RESULT_QUEUE_URL=${RESULT_QUEUE_URL}}"

SUBMISSION_QUEUE_ARN=$(aws sqs get-queue-attributes --queue-url ${SUBMISSION_QUEUE_URL} --attribute-names QueueArn --query 'Attributes.QueueArn' --output text)

aws lambda create-event-source-mapping \
  --function-name dispatcher-lambda \
  --batch-size 1 \
  --event-source-arn ${SUBMISSION_QUEUE_ARN}
```

### Step 7: Callback Lambda

📁 [callback/app.py](./callback-lambda/app.py)

```bash
mkdir callback-lambda && cd callback-lambda
zip ../callback.zip app.py && cd ..

aws lambda create-function \
  --function-name callback-lambda \
  --runtime python3.9 \
  --handler app.handler \
  --role ${ROLE_ARN} \
  --zip-file fileb://callback.zip

RESULT_QUEUE_ARN=$(aws sqs get-queue-attributes --queue-url ${RESULT_QUEUE_URL} --attribute-names QueueArn --query 'Attributes.QueueArn' --output text)

aws lambda create-event-source-mapping \
  --function-name callback-lambda \
  --batch-size 1 \
  --event-source-arn ${RESULT_QUEUE_ARN}
```

### Step 8: Submit Lambda

📁 [submit/app.py](./submit-lambda/app.py)

```bash
mkdir submit-lambda && cd submit-lambda
zip ../submit.zip app.py && cd ..

aws lambda create-function \
  --function-name submit-lambda \
  --runtime python3.9 \
  --handler app.handler \
  --role ${ROLE_ARN} \
  --zip-file fileb://submit.zip \
  --environment "Variables={SUBMISSION_QUEUE_URL=${SUBMISSION_QUEUE_URL}}"
```

---

## Part 4: API Gateway

### Step 9: Expose Submit Endpoint

```bash
API_ID=$(aws apigateway create-rest-api --name "Judge API" --query 'id' --output text)
PARENT_ID=$(aws apigateway get-resources --rest-api-id ${API_ID} --query 'items[0].id' --output text)
RESOURCE_ID=$(aws apigateway create-resource --rest-api-id ${API_ID} --parent-id ${PARENT_ID} --path-part "submit" --query 'id' --output text)

aws apigateway put-method \
  --rest-api-id ${API_ID} \
  --resource-id ${RESOURCE_ID} \
  --http-method POST \
  --authorization-type "NONE"

SUBMIT_LAMBDA_ARN=$(aws lambda get-function --function-name submit-lambda --query 'Configuration.FunctionArn' --output text)

aws apigateway put-integration \
  --rest-api-id ${API_ID} \
  --resource-id ${RESOURCE_ID} \
  --http-method POST \
  --type AWS_PROXY \
  --integration-http-method POST \
  --uri arn:aws:apigateway:${AWS_REGION}:lambda:path/2015-03-31/functions/${SUBMIT_LAMBDA_ARN}/invocations

aws lambda add-permission \
  --function-name submit-lambda \
  --statement-id "apigw-invoke-permission-$(uuidgen)" \
  --action "lambda:InvokeFunction" \
  --principal "apigateway.amazonaws.com" \
  --source-arn "arn:aws:execute-api:${AWS_REGION}:${AWS_ACCOUNT_ID}:${API_ID}/*/*/*"

aws apigateway create-deployment \
  --rest-api-id ${API_ID} \
  --stage-name "v1"

# Final API endpoint
# https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/v1/submit
```

---

## Part 5: End-to-End Testing

### Step 10: Test System

📁 Sample Payload:
```bash
curl -X POST \
  https://${API_ID}.execute-api.${AWS_REGION}.amazonaws.com/v1/submit \
  -H "Content-Type: application/json" \
  -d '
  {
    "language": "cpp",
    "sourceCode": "#include <iostream> ...",
    "input": "world",
    "expectedOutput": "Hello, world",
    "callbackUrl": "https://webhook.site/..."
   } 
  '
```

---