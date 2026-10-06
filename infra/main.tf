module "dynamodb" {
  source     = "./modules/dynamodb"
  table_name = "${var.project}-memory"
}

module "lambda" {
  source = "./modules/lambda"

  function_name      = "${var.project}-assistant"
  source_dir         = "${path.module}/../lambda"
  table_name         = module.dynamodb.table_name
  table_arn          = module.dynamodb.table_arn
  model_id           = var.model_id
  foundation_model   = var.foundation_model
  bedrock_regions    = var.bedrock_regions
  log_retention_days = var.log_retention_days
}