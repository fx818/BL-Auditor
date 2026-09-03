Example:
{
  "order": {
    "product": "Cooking Oil",
    "quantity": 20,
    "unit": "Litre"
  },
  "answer": {
    "classification": "Non-Retail",
    "confidence": "Medium",
    "reasoning": "While cooking oil is consumer-oriented, this quantity exceeds typical end-use and is more consistent with procurement for commercial food operations or redistribution rather than individual or micro-scale usage."
  }
}

Example:
{
  "order": {
    "product": "Industrial Solvent",
    "quantity": 2,
    "unit": "Litre"
  },
  "answer": {
    "classification": "Non-Retail",
    "confidence": "High",
    "reasoning": "Primarily an industrial product regardless of quantity."
  }
}

Example:
{
  "order": {
    "product": "Wheat",
    "quantity": 1,
    "unit": "Quintal"
  },
  "answer": {
    "classification": "Non-Retail",
    "confidence": "High",
    "reasoning": "Use of trade units like quintal strongly indicates procurement for resale, storage, or commercial handling rather than end consumption."
  }
}

Example:
{
  "order": {
    "product": "Dry Fruits",
    "quantity": 2,
    "unit": "KG"
  },
  "answer": {
    "classification": "Retail",
    "confidence": "Low",
    "reasoning": "Could be personal use or small resale; intent is ambiguous."
  }
}

Example:
{
  "order": {
    "product": "Dry Fruits",
    "quantity": 10,
    "unit": "KG"
  },
  "answer": {
    "classification": "Non-Retail",
    "confidence": "Medium",
    "reasoning": "Quantity suggests resale-scale purchasing beyond typical end consumption."
  }
}

Example:
{
  "order": {
    "product": "Power Drill Machine",
    "quantity": 1,
    "unit": "Piece"
  },
  "answer": {
    "classification": "Retail",
    "confidence": "Medium",
    "reasoning": "Single-unit tool purchase indicates individual or small professional use."
  }
}

Example:
{
  "order": {
    "product": "Cleaning Chemical",
    "quantity": 1,
    "unit": "Drum"
  },
  "answer": {
    "classification": "Non-Retail",
    "confidence": "High",
    "reasoning": "Industrial packaging strongly indicates commercial usage."
  }
}
