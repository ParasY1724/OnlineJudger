# Scalable Cloud-Native Serverless Online Judge

This repository contains the source code for a highly scalable, fault-tolerant, and serverless online judge system built on AWS. It is designed to concurrently process a massive number of code submissions using a decoupled, microservices-based architecture.

## System Design

## Key Features

  * **Massive Concurrency**: Capable of judging thousands of submissions in parallel.
  * **Secure Sandboxing**: Each submission runs in a completely isolated container environment.
  * **Language Agnostic**: Supports compiled (C++, Java) and interpreted (Python, JavaScript) languages.
  * **Decoupled & Resilient**: Uses message queues to ensure no submission is lost during traffic spikes or service failures.
  * **Asynchronous Notifications**: Employs webhooks (callbacks) to notify users of results without polling.
  * **Cost-Effective**: A serverless-first design means you only pay for the exact compute time used.
-----
## Implementation

For detailed implementation instructions, please refer to the [Implementation Docs](./path/to/implementation/README.md).

-----

### Serverless-First Architecture

This project minimizes operational overhead by avoiding manually managed servers.

  * **AWS Lambda**: Used for all event-driven logic, such as submission intake, dispatching tasks, and sending callbacks.
  * **AWS Fargate**: Provides serverless compute for containers, so you can run the judge without provisioning or managing EC2 instances.
  * **Amazon DynamoDB & SQS**: Fully managed, auto-scaling services for database and messaging, eliminating the need for maintenance.

-----

### Horizontal Scalability

The system is designed to scale horizontally at every stage:

  * **API Gateway & Lambda**: The submission endpoint can handle virtually any number of simultaneous incoming requests by automatically scaling the number of Lambda instances.
  * **SQS Queues**: The submission and result queues act as elastic buffers that can grow to accommodate millions of messages, allowing the judging backend to process jobs at its own pace.
  * **AWS Fargate**: The core of the scalability lies in launching a new Fargate task for every submission. This allows for massive parallel processing, limited only by your AWS account limits.

-----

### Fault Tolerance

Resilience is built into the architecture:

  * **Decoupled Services**: If the judging mechanism (Fargate) or the notification service fails, submissions are safely held in the SQS queues until the downstream services recover.
  * **Isolated Execution**: A crash or error in one judging task has no impact on any other concurrent tasks.
  * **Managed Services**: By leveraging managed AWS services like SQS, DynamoDB, and Fargate, we delegate the responsibility of maintaining high availability to AWS.

-----
