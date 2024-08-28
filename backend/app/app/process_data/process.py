import pandas as pd
import numpy as np
import calendar
from app.api.deps import DateFilterType
from datetime import datetime
from .utils import get_month_weeks, get_week_range, return_base

def get_percentage(past, actual):
    if past == actual or actual == 0:
        return 0

    if past == 0:
        return 100

    return round(((actual - past) / abs(past)) * 100, 2)

def get_df(expenses, incomes, accounts, places, categories):
    def get_account_id(row):
        return row['account_id']

    def get_category_name(row):
        category_id = row['category_id']
        if pd.notna(category_id) and category_id in categories_df.index:
            return categories_df.loc[category_id, 'name']

        return None

    def get_subcategory_name(row, income=False):
        subcategory_id = row['subcategory_id']
        if pd.notna(subcategory_id) and subcategory_id in subcategories_df.index:
            return subcategories_df.loc[subcategory_id, 'name']

        return None

    def get_place_name(row):
        place_id = row['place_id']
        if pd.notna(place_id) and place_id in places_df.index:
            return places_df.loc[place_id, 'name']

        return None


    incomes_df = pd.DataFrame(incomes)
    expenses_df = pd.DataFrame(expenses)
    accounts_df = pd.DataFrame(accounts)
    places_df = pd.DataFrame(places)
    categories_df = pd.DataFrame(categories)

    categories_df.set_index('id', inplace=True)

    subcategories = []
    for category in categories_df.index:
        for subcategory in categories_df.loc[category, 'subcategories']:
            subcategories.append(subcategory)

    subcategories_df = pd.DataFrame(subcategories)
    subcategories_df.set_index('id', inplace=True)

    if not places_df.empty:
        places_df.set_index('id', inplace=True)

    if not accounts_df.empty:
        accounts_df.set_index('id', inplace=True)

    if not expenses_df.empty:
        expenses_df['type'] = 'expense'
        expenses_df['amount'] = -expenses_df['amount']
        expenses_df.set_index('id', inplace=True)

        expenses_df['account'] = expenses_df.apply(get_account_id, axis=1)
        expenses_df['category'] = expenses_df.apply(get_category_name, axis=1)
        expenses_df['place'] = expenses_df.apply(get_place_name, axis=1)
        expenses_df['subcategory'] = expenses_df.apply(get_subcategory_name, axis=1)

        expenses_df.drop(columns=['account_id', 'category_id', 'place_id', 'subcategory_id', 'owner_id'], inplace=True)


    if not incomes_df.empty:
        incomes_df['type'] = 'income'
        incomes_df.set_index('id', inplace=True)

        incomes_df['account'] = incomes_df.apply(get_account_id, axis=1)
        incomes_df['place'] = incomes_df.apply(get_place_name, axis=1)
        # TODO: add category to incomes database with id that corresponds to Ingresos
        incomes_df['subcategory'] = incomes_df.apply(get_subcategory_name, axis=1)

        incomes_df.drop(columns=['account_id', 'place_id', 'subcategory_id', 'owner_id'], inplace=True)

    return { 'expenses': expenses_df, 'incomes': incomes_df, 'accounts': accounts_df, 'places': places_df, 'categories': categories_df, 'subcategories': subcategories_df }


def transaction_charts(date_filter_type, incomes_df, expenses_df):
    combined_df = pd.concat([expenses_df, incomes_df], ignore_index=True)

    # Make df with all months
    all_months = pd.DataFrame({'month': calendar.month_name[1:]})


    # Convert date to datetime and add month column
    combined_df['date'] = pd.to_datetime(combined_df['date'])
    combined_df['month'] = combined_df['date'].dt.strftime('%B')

    # Sort by date
    combined_df.sort_values(by='date', inplace=True)

    # Add quarter column (quarter)
    combined_df['quarter'] = combined_df['date'].dt.quarter

    # Add day of week column (week)
    combined_df['day_of_week'] = combined_df['date'].dt.day_name()

    # Add week number (month)
    combined_df['week'] = combined_df['date'].dt.strftime('%W')
    combined_df['week'] = combined_df['week'].astype(np.int8)


    if date_filter_type == DateFilterType.year:
        total = all_months.merge(combined_df.groupby('month', as_index=False)['amount'].sum(), on='month', how='left').fillna(0)
        expenses = all_months.merge(combined_df[combined_df['type'] == 'expense'].groupby('month', as_index=False)['amount'].sum(), on='month', how='left').fillna(0)
        expenses['amount'] = expenses['amount'].abs()
        incomes = all_months.merge(combined_df[combined_df['type'] == 'income'].groupby('month', as_index=False)['amount'].sum(), on='month', how='left').fillna(0)

        return return_base(xAxis=total['month'].tolist(), total=total['amount'].tolist(), expenses=expenses['amount'].tolist(), incomes=incomes['amount'].tolist())


    if date_filter_type == DateFilterType.quarter:
        all_months['quarter'] = all_months['month'].map({
            'January': 1, 'February': 1, 'March': 1,
            'April': 2, 'May': 2, 'June': 2,
            'July': 3, 'August': 3, 'September': 3,
            'October': 4, 'November': 4, 'December': 4
        })

        quarter_total = all_months.merge(combined_df.groupby('month', as_index=False)['amount'].sum(), on='month', how='left')
        quarter_total = quarter_total[quarter_total['quarter'] == quarter_total.iloc[0]['quarter']].fillna(0)
        quarter_expenses = all_months.merge(combined_df[combined_df['type'] == 'expense'].groupby('month', as_index=False)['amount'].sum(), on='month', how='left')
        quarter_expenses = quarter_expenses[quarter_expenses['quarter'] == quarter_expenses.iloc[0]['quarter']].fillna(0)
        quarter_incomes = all_months.merge(combined_df[combined_df['type'] == 'income'].groupby('month', as_index=False)['amount'].sum(), on='month', how='left')
        quarter_incomes = quarter_incomes[quarter_incomes['quarter'] == quarter_incomes.iloc[0]['quarter']].fillna(0)

        quarter_expenses['amount'].abs()
        all_months.loc[all_months['quarter'] == combined_df.iloc[0]['quarter']] = all_months.loc[all_months['quarter'] == combined_df.iloc[0]['quarter']].fillna(0)

        filter_quarter = quarter_total[~pd.isna(quarter_total['amount'])]

        return return_base(xAxis=filter_quarter['month'].tolist(),
                           total=filter_quarter['amount'].tolist(),
                           expenses=quarter_expenses[~pd.isna(quarter_expenses['amount'])]['amount'].tolist(),
                           incomes=quarter_incomes[~pd.isna(quarter_incomes['amount'])]['amount'].tolist())


    if date_filter_type == DateFilterType.month:
        month_ranges = {
            'week': [],
            'range': []
        }

        week_start, week_end = get_month_weeks(combined_df.iloc[0]['date'].year, combined_df.iloc[0]['date'].month)

        for i in range(week_start, week_end + 1):
            data = get_week_range(combined_df.iloc[0]['date'].year, i)
            month_ranges['week'].append(data['week'])
            month_ranges['range'].append(data['range'])

        month_df = pd.DataFrame(month_ranges)

        month_df = pd.DataFrame(month_ranges)

        month_total = month_df.merge(combined_df.groupby('week', as_index=False)['amount'].sum(), on='week', how='left').fillna(0)
        month_incomes = month_df.merge(combined_df[combined_df['type'] == 'income'].groupby('week', as_index=False)['amount'].sum(), on='week', how='left').fillna(0)
        month_expenses = month_df.merge(combined_df[combined_df['type'] == 'expense'].groupby('week', as_index=False)['amount'].sum(), on='week', how='left').fillna(0)
        month_expenses['amount'].abs()

        return return_base(xAxis=month_total['range'].tolist(),
                           total=month_total['amount'].tolist(),
                           expenses=month_incomes['amount'].tolist(),
                           incomes=month_expenses['amount'].tolist())


    if date_filter_type == DateFilterType.week:
        all_days = pd.DataFrame({'day_of_week': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']})

        week_total = all_days.merge(combined_df.groupby('day_of_week', as_index=False)['amount'].sum(), on='day_of_week', how='left').fillna(0)
        week_incomes = all_days.merge(combined_df[combined_df['type'] == 'income'].groupby('day_of_week', as_index=False)['amount'].sum(), on='day_of_week', how='left').fillna(0)
        expenses['amount'].abs()
        week_expenses = all_days.merge(combined_df[combined_df['type'] == 'expense'].groupby('day_of_week', as_index=False)['amount'].sum(), on='day_of_week', how='left').fillna(0)
        week_expenses['amount'].abs()

        return return_base(xAxis=week_total['range'].tolist(),
                           total=week_total['amount'].tolist(),
                           expenses=week_incomes['amount'].tolist(),
                           incomes=week_expenses['amount'].tolist())


    if date_filter_type == DateFilterType.date or date_filter_type == DateFilterType.range:
            date_total = combined_df.groupby('date', as_index=False)['amount'].sum()
            date_incomes = combined_df[combined_df['type'] == 'income'].groupby('date', as_index=False)['amount'].sum()
            date_expenses = combined_df[combined_df['type'] == 'expense'].groupby('date', as_index=False)['amount'].sum()

            date_expenses['amount'] = date_expenses['amount'].abs()

            # convert datetime to string YYYY-MM-DD
            date_total['date'] = date_total['date'].dt.strftime('%Y-%m-%d')
            date_incomes['date'] = date_incomes['date'].dt.strftime('%Y-%m-%d')
            date_expenses['date'] = date_expenses['date'].dt.strftime('%Y-%m-%d')

            return return_base(xAxis=date_total['date'].tolist(),
                            total=date_total['amount'].tolist(),
                            expenses=date_expenses['amount'].tolist(),
                            incomes=date_incomes['amount'].tolist())


def categories_charts(incomes, expenses):
    if incomes.empty and expenses.empty:
        return None

    if incomes.empty:
        incomes = pd.DataFrame(columns=['category', 'subcategory', 'amount'])

    if expenses.empty:
        expenses = pd.DataFrame(columns=['category', 'subcategory', 'amount'])


    expenses_subandcats = expenses.groupby(['category', 'subcategory'])['amount'].sum().fillna(0).reset_index()
    expenses_subandcats['amount'] = expenses_subandcats['amount'].abs()

    incomes_to_add = {'category': 'Ingresos', 'subcategory': 'Ingresos', 'amount': incomes['amount'].sum()}
    expenses_subandcats.loc[len(expenses_subandcats)] = incomes_to_add


    incomes_subandcats = incomes.groupby(['subcategory'])['amount'].sum().fillna(0).reset_index()
    incomes_subandcats['category'] = 'Ingresos'


    combined_df = pd.concat([expenses_subandcats, incomes_subandcats], ignore_index=True)


    result = []

    for category, group in combined_df.groupby('category'):
        category_data = {'name': category, 'data': []}

        for _, row in group.iterrows():
            if row['subcategory'] != 'Ingresos':
                subcategory_data = {'name': row['subcategory'], 'value': row['amount']}
                category_data['data'].append(subcategory_data)

        result.append(category_data)

    cats_df = expenses_subandcats.groupby(['category'])['amount'].sum().fillna(0).reset_index()
    return {
        'drilldown': result,
        'categories': [{'name': row['category'], 'value': row['amount']} for _, row in cats_df.iterrows()]
    }


def accounts_total(incomes_df, expenses_df):
    if incomes_df.empty and expenses_df.empty:
        return {}

    if incomes_df.empty:
        return expenses_df.groupby('account')['amount'].sum().to_dict()

    if expenses_df.empty:
        return incomes_df.groupby('account')['amount'].sum().to_dict()

    combined_df = pd.concat([expenses_df, incomes_df], sort=False)

    return combined_df.groupby('account')['amount'].sum().to_dict()

def account_diff(past, actual):
    result = {}

    for account, value in actual.items():
        if account in past:
            result[account] = get_percentage(past[account], value)
        else:
            result[account] = 100

    return result

def account_charts(incomes_df, expenses_df):
    if incomes_df.empty and expenses_df.empty:
        return {}

    if incomes_df.empty:
        df = expenses_df.groupby(['account', 'date'])['amount'].sum().to_dict()
    elif expenses_df.empty:
        df = incomes_df.groupby(['account', 'date'])['amount'].sum().to_dict()
    else:
        combined_df = pd.concat([expenses_df, incomes_df], sort=False)
        df = combined_df.groupby(['account', 'date'])['amount'].sum().to_dict()

    result = {}

    for account, value in df.items():
        if account[0] not in result:
            result[account[0]] = {
                'xAxis': {
                    'data': []
                },
                'series': {
                    'data': []
                }
            }

        result[account[0]]['xAxis']['data'].append(datetime.strptime(account[1],'%Y-%m-%d').date())
        result[account[0]]['series']['data'].append(value)

    return result