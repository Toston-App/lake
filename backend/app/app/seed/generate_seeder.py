# Using faker, generate fake data for the database on a JSON file.

import json
from faker import Faker

fake = Faker()

def gen_accounts(n):
    accounts = []
    for _ in range(n):
        account = {}
        account['name'] = fake.unique.company()
        account['initial_balance'] = fake.pyfloat(left_digits=5, right_digits=2, min_value=0)
        accounts.append(account)

    with open('accounts.json', 'w') as outfile:
        json.dump(accounts, outfile)

def gen_places(n):
    placesList = []
    for _ in range(n):
        places = {}
        places['name'] = fake.unique.company()
        places['is_online'] = fake.pybool()
        placesList.append(places)

    with open('places.json', 'w') as outfile:
        json.dump(placesList, outfile)

def gen_expenses(n):
    expensesList = []
    for _ in range(n):
        expenses = {}
        expenses['description'] = fake.paragraph(nb_sentences=1, variable_nb_sentences=True, ext_word_list=None)
        expenses['amount'] = fake.pyfloat(left_digits=3, right_digits=2, min_value=0)
        expenses['date'] = fake.date_between(start_date='-1y', end_date='today').strftime('%Y-%m-%d')
        expenses['account_id'] = fake.random_int(min=1, max=10)
        expenses['category_id'] = fake.random_int(min=1, max=10)
        expenses['subcategory_id'] = fake.random_int(min=1, max=10)
        expenses['place_id'] = fake.random_int(min=1, max=10)
        expensesList.append(expenses)

    with open('expenses.json', 'w') as outfile:
        json.dump(expensesList, outfile)

def gen_incomes(n):
    incomesList = []
    for _ in range(n):
        incomes = {}
        incomes['description'] = fake.paragraph(nb_sentences=1, variable_nb_sentences=True, ext_word_list=None)
        incomes['amount'] = fake.pyfloat(left_digits=3, right_digits=2, min_value=0)
        incomes['date'] = fake.date_between(start_date='-1y', end_date='today').strftime('%Y-%m-%d')
        incomes['account_id'] = fake.random_int(min=1, max=10)
        incomes['subcategory_id'] = fake.random_int(min=78, max=89)
        incomes['place_id'] = fake.random_int(min=1, max=10)
        incomesList.append(incomes)

    with open('incomes.json', 'w') as outfile:
        json.dump(incomesList, outfile)

def gen_transfers(n):
    transfersList = []
    for _ in range(n):
        transfers = {}
        transfers['description'] = fake.paragraph(nb_sentences=1, variable_nb_sentences=True, ext_word_list=None)
        transfers['amount'] = fake.pyfloat(left_digits=5, right_digits=2, min_value=0)
        transfers['from_acc'] = fake.random_int(min=1, max=10)
        transfers['to_acc'] = fake.random_int(min=1, max=10)
        transfersList.append(transfers)

    with open('transfers.json', 'w') as outfile:
        json.dump(transfersList, outfile)


gen_accounts(10) # If this value is changed, change the max value in account in gen_expenses, incomes and transfers
gen_places(10)
gen_expenses(10)
gen_incomes(10)
gen_transfers(30)